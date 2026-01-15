"""
Sparse vector data migration script

Used to migrate historical data to sparse vector format.

Usage:
    from powermem import Memory, auto_config
    from script import ScriptManager
    
    config = auto_config()
    memory = Memory(config=config)
    
    ScriptManager.run('migrate-sparse-vector', memory, batch_size=1000, workers=3, dry_run=False)
"""
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Any, Dict

from sqlalchemy import text

from powermem.utils import OceanBaseUtil

logger = logging.getLogger(__name__)


class SparseMigrationWorker:
    """
    Sparse vector migration Worker
    """

    def __init__(
        self,
        memory,
        batch_size: int = 1000,
        delay: float = 0.1,
        workers: int = 1,
        dry_run: bool = False
    ):
        """
        Initialize Worker
        
        Args:
            memory: Memory object instance
            batch_size: Batch processing size
            delay: Delay between batches (seconds)
            workers: Thread pool size for concurrent processing (default 1)
            dry_run: Whether to run in dry-run mode (test with 100 records)
        """
        self.memory = memory
        self.batch_size = batch_size
        self.delay = delay
        self.workers = workers
        self.dry_run = dry_run

        # Parameter validation
        if workers < 1:
            raise ValueError(f"workers must be >= 1, got {workers}")

        # Statistics (thread-safe)
        self.total_count = 0
        self.migrated_count = 0
        self.failed_count = 0
        self.start_time = None
        self._lock = threading.Lock()  # Lock to protect statistics
        
        self.worker_progress = {}
        self._stop_progress_display = threading.Event()

        # Get necessary components from Memory object
        self._init_from_memory()

    def _init_from_memory(self):
        """Initialize components from Memory object"""
        # Get storage
        self.storage = self.memory.storage
        if not hasattr(self.storage.vector_store, 'obvector'):
            raise ValueError("Memory storage must be OceanBaseVectorStore")

        # Get database engine
        self.engine = self.storage.vector_store.obvector.engine
        self.table_name = self.storage.collection_name
        self.text_field = self.storage.vector_store.text_field

        # Get sparse embedder
        self.sparse_embedder = getattr(self.memory, 'sparse_embedder', None)
        if not self.sparse_embedder:
            raise ValueError(
                "sparse_embedder not found in Memory object. "
                "Please configure sparse_embedder in your config."
            )

        # Get audit log
        self.audit = getattr(self.memory, 'audit', None)

        logger.info(f"Initialized migration worker for sparse vector")
        logger.info(f"  Database table: {self.table_name}")
        logger.info(f"  Text field: {self.text_field}")
        logger.info(f"  Thread pool size: {self.workers}")

    def _fetch_all_pending_ids(self) -> List[int]:
        """
        Get all pending record IDs to migrate
        
        Returns:
            List of IDs (sorted by id)
        """
        with self.engine.connect() as conn:
            result = conn.execute(text(
                f"SELECT id FROM {self.table_name} "
                f"WHERE sparse_embedding IS NULL "
                f"ORDER BY id"
            ))
            return [row[0] for row in result]

    def _fetch_records_by_ids(self, ids: List[int]) -> List[Dict]:
        """
        Get record data by ID list
        
        Args:
            ids: List of IDs
        
        Returns:
            List of records [{'id': ..., 'text_content': ...}, ...]
        """
        if not ids:
            return []

        # Build parameters for IN clause
        placeholders = ', '.join([f':id_{i}' for i in range(len(ids))])
        params = {f'id_{i}': id_val for i, id_val in enumerate(ids)}

        with self.engine.connect() as conn:
            result = conn.execute(text(
                f"SELECT id, {self.text_field} as text_content "
                f"FROM {self.table_name} "
                f"WHERE id IN ({placeholders})"
            ), params)

            return [dict(row._mapping) for row in result]

    def _compute_sparse_embeddings(self, texts: List[str]) -> List[Optional[Dict[int, float]]]:
        """
        Batch compute sparse vectors
        
        Args:
            texts: List of texts
        
        Returns:
            List of sparse vectors (None for failed ones)
        """
        results = []
        for text in texts:
            try:
                if not text or not text.strip():
                    results.append(None)
                    continue

                sparse_embedding = self.sparse_embedder.embed_sparse(text)
                results.append(sparse_embedding)
            except Exception as e:
                logger.warning(f"Failed to compute sparse embedding: {e}")
                results.append(None)

        return results

    def _update_batch(self, updates: List[Dict]) -> int:
        """
        Batch update database
        
        Args:
            updates: List of updates [{'id': ..., 'sparse_embedding': ...}, ...]
        
        Returns:
            Number of successfully updated records
        """
        if self.dry_run:
            return len(updates)

        success_count = 0

        with self.engine.connect() as conn:
            for update in updates:
                try:
                    # Skip records with failed computation (None), don't write to database
                    if update['sparse_embedding'] is None:
                        logger.warning(f"Record {update['id']} has no valid sparse embedding, skipping")
                        continue

                    sparse_str = OceanBaseUtil.format_sparse_vector(update['sparse_embedding'])

                    conn.execute(text(
                        f"UPDATE {self.table_name} "
                        f"SET sparse_embedding = '{sparse_str}' "
                        f"WHERE id = :id"
                    ), {'id': update['id']})
                    success_count += 1
                except Exception as e:
                    logger.error(f"Failed to update record {update['id']}: {e}")

            conn.commit()

        return success_count

    def _format_duration(self, seconds: float) -> str:
        """Format duration"""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"

    def _draw_progress_bar(self, percentage: float, width: int = 14) -> str:
        """
        Draw progress bar
        
        Args:
            percentage: Percentage (0-100)
            width: Progress bar width (number of characters)
        
        Returns:
            Progress bar string
        """
        filled = int(width * percentage / 100)
        empty = width - filled
        bar = '█' * filled + '░' * empty
        return f"[{bar}]"

    def _display_progress(self):
        """Display current progress (with progress bar)"""
        # Clear previous output (move cursor)
        if hasattr(self, '_last_line_count'):
            # Move up N lines and clear
            sys.stdout.write(f'\033[{self._last_line_count}A')
            sys.stdout.write('\033[J')
        
        lines = []
        
        # Overall progress
        if self.total_count > 0:
            total_processed = self.migrated_count + self.failed_count
            total_percent = (total_processed / self.total_count) * 100
        else:
            total_processed = 0
            total_percent = 0
        
        progress_bar = self._draw_progress_bar(total_percent)
        lines.append(
            f"\nTotal: {progress_bar} {total_percent:5.1f}% | "
            f"{total_processed:,}/{self.total_count:,}"
        )
        
        # Status information
        elapsed = time.time() - self.start_time
        speed = self.migrated_count / elapsed if elapsed > 0 else 0
        
        # Estimated remaining time
        remaining_records = self.total_count - total_processed
        if speed > 0:
            remaining_seconds = remaining_records / speed
            remaining_str = self._format_duration(remaining_seconds)
        else:
            remaining_str = "N/A"
        
        lines.append(f"  ✓ Migrated: {self.migrated_count:,} | ✗ Failed: {self.failed_count}")
        lines.append(f"  ⏱ Elapsed: {self._format_duration(elapsed)} | Remaining: ~{remaining_str} | 📊 {speed:.1f} rec/s")
        
        # Display progress for each worker (only show processed and failed counts)
        if self.workers > 1 and self.worker_progress:
            lines.append("")
            lines.append(f"Workers ({self.workers}):")
            for worker_id in sorted(self.worker_progress.keys()):
                progress = self.worker_progress[worker_id]
                processed = progress.get('processed', 0)
                failed = progress.get('failed', 0)
                lines.append(f"  Worker {worker_id}: ✓ {processed:,} | ✗ {failed}")
        
        # Output
        output = '\n'.join(lines)
        sys.stdout.write(output)
        sys.stdout.flush()
        
        # Record line count for next clear
        self._last_line_count = len(lines)

    def _progress_display_loop(self):
        """Progress display loop (runs in separate thread)"""
        while not self._stop_progress_display.is_set():
            with self._lock:
                self._display_progress()
            
            # Update every second
            self._stop_progress_display.wait(timeout=1.0)

    def _run_worker(self, worker_id: int, assigned_ids: List[int]) -> Dict[str, int]:
        """
        Execution logic for a single worker
        
        Args:
            worker_id: Worker ID (0, 1, 2, ...)
            assigned_ids: List of IDs assigned to this worker
        
        Returns:
            Statistics {'migrated': ..., 'failed': ...}
        """
        worker_migrated = 0
        worker_failed = 0
        
        # Initialize progress for this worker
        with self._lock:
            self.worker_progress[worker_id] = {
                'processed': 0,
                'failed': 0
            }

        logger.info(f"Worker {worker_id} started (thread: {threading.current_thread().name}), assigned: {len(assigned_ids)}")

        # Process assigned IDs in batches
        for i in range(0, len(assigned_ids), self.batch_size):
            batch_ids = assigned_ids[i:i + self.batch_size]
            
            # Get record data by IDs
            batch = self._fetch_records_by_ids(batch_ids)

            if not batch:
                continue

            # Extract texts
            texts = [record['text_content'] for record in batch]

            # Compute sparse vectors
            sparse_embeddings = self._compute_sparse_embeddings(texts)

            # Build update data
            batch_failed = 0
            updates = []
            for record, sparse_emb in zip(batch, sparse_embeddings):
                updates.append({
                    'id': record['id'],
                    'sparse_embedding': sparse_emb
                })
                if sparse_emb is None:
                    batch_failed += 1

            # Batch update
            success_count = self._update_batch(updates)
            worker_migrated += success_count
            worker_failed += batch_failed

            # Update global statistics and worker progress (thread-safe)
            with self._lock:
                self.migrated_count += success_count
                self.failed_count += batch_failed
                
                # Update this worker's progress
                self.worker_progress[worker_id]['processed'] += len(batch)
                self.worker_progress[worker_id]['failed'] += batch_failed

            # dry-run mode only runs once
            if self.dry_run:
                break

            # Delay control
            if self.delay > 0:
                time.sleep(self.delay)

        logger.info(f"Worker {worker_id} completed: migrated={worker_migrated}, failed={worker_failed}")

        return {
            'worker_id': worker_id,
            'migrated': worker_migrated,
            'failed': worker_failed
        }

    def _log_audit_event(self, status: str, details: Dict[str, Any]):
        """Log audit event"""
        if self.audit is None:
            return

        try:
            self.audit.log_security_event(
                event_type='sparse_migration_progress',
                severity='info' if status != 'failed' else 'error',
                details=details
            )
        except Exception as e:
            logger.warning(f"Failed to log audit event: {e}")

    def run(self):
        """Execute migration (using thread pool for concurrency)"""
        self.start_time = time.time()

        # Preload all pending IDs (avoid cursor sliding issues)
        logger.info("Fetching all pending IDs...")
        all_pending_ids = self._fetch_all_pending_ids()
        self.total_count = len(all_pending_ids)

        logger.info(f"Starting sparse vector migration")
        logger.info(f"Total records to migrate: {self.total_count}")
        logger.info(f"Batch size: {self.batch_size}")
        logger.info(f"Thread pool size: {self.workers}")

        if self.dry_run:
            logger.info("[DRY RUN] Mode enabled - will only test with 100 records")
            # dry-run mode only takes first 100
            all_pending_ids = all_pending_ids[:100]
            self.total_count = len(all_pending_ids)

        # Assign IDs to each worker (round-robin for even distribution)
        worker_id_assignments: List[List[int]] = [[] for _ in range(self.workers)]
        for i, record_id in enumerate(all_pending_ids):
            worker_id_assignments[i % self.workers].append(record_id)

        for worker_id, assigned_ids in enumerate(worker_id_assignments):
            logger.info(f"Worker {worker_id} assigned {len(assigned_ids)} records")

        # Log start event
        self._log_audit_event('started', {
            'status': 'started',
            'total_records': self.total_count,
            'batch_size': self.batch_size,
            'workers': self.workers,
            'dry_run': self.dry_run
        })

        # Start progress display thread
        progress_thread = threading.Thread(target=self._progress_display_loop, daemon=True)
        progress_thread.start()

        try:
            if self.workers == 1:
                # Single-threaded mode: execute directly
                logger.info("Single-threaded mode")
                self._run_worker(worker_id=0, assigned_ids=worker_id_assignments[0])
            else:
                # Multi-threaded mode: use thread pool
                logger.info(f"Multi-threaded mode: launching {self.workers} workers")

                with ThreadPoolExecutor(max_workers=self.workers) as executor:
                    # Submit all worker tasks with assigned ID lists
                    futures = {}
                    for worker_id in range(self.workers):
                        future = executor.submit(
                            self._run_worker, 
                            worker_id, 
                            worker_id_assignments[worker_id]
                        )
                        futures[future] = worker_id

                    # Wait for all tasks to complete
                    for future in as_completed(futures):
                        worker_id = futures[future]
                        try:
                            result = future.result()
                            logger.info(f"Worker {worker_id} finished: {result}")
                        except Exception as e:
                            logger.error(f"Worker {worker_id} failed: {e}", exc_info=True)

            # Stop progress display
            self._stop_progress_display.set()
            progress_thread.join(timeout=2.0)
            
            # Display progress one last time
            with self._lock:
                self._display_progress()
            
            print("\n")  # Newline to avoid subsequent logs overwriting progress

            # Log successful completion event
            duration = time.time() - self.start_time
            self._log_audit_event('completed', {
                'status': 'completed',
                'total_records': self.total_count,
                'migrated_count': self.migrated_count,
                'failed_count': self.failed_count,
                'duration_seconds': duration
            })

            logger.info("=" * 50)
            logger.info("Migration completed!")
            logger.info(f"  Total migrated: {self.migrated_count}")
            logger.info(f"  Total failed: {self.failed_count}")
            logger.info(f"  Duration: {self._format_duration(duration)}")
            if self.workers > 1:
                speed = self.migrated_count / duration if duration > 0 else 0
                logger.info(f"  Average speed: {speed:.1f} records/sec")

        except Exception as e:
            # Stop progress display
            self._stop_progress_display.set()
            progress_thread.join(timeout=2.0)
            
            # Log failure event
            self._log_audit_event('failed', {
                'status': 'failed',
                'error': str(e),
                'migrated_count': self.migrated_count,
                'failed_count': self.failed_count
            })
            raise



def migrate_sparse_vector(
    memory: 'Memory',
    batch_size: int = 100,
    delay: float = 0.1,
    workers: int = 1,
    dry_run: bool = False
) -> bool:
    """
    Migrate historical data to add sparse vectors
    
    Args:
        memory: Memory object instance (required)
        batch_size: Batch processing size (default 1000)
        delay: Delay between batches in seconds (default 0.1)
        workers: Thread pool size (default 1)
        dry_run: Whether to run in dry-run mode, only test 100 records (default False)
    
    Returns:
        bool: Returns True on success, False on failure
    
    Example:
        ```python
        from powermem import Memory, auto_config
        from script import ScriptManager
        
        config = auto_config()
        memory = Memory(config=config)
        
        ScriptManager.run('migrate-sparse-vector', memory, batch_size=1000, workers=3)
        
        # Or call directly
        from script.scripts.migrate_sparse_vector import migrate_sparse_vector
        migrate_sparse_vector(memory, workers=3)
        ```
    """
    try:
        worker = SparseMigrationWorker(
            memory=memory,
            batch_size=batch_size,
            delay=delay,
            workers=workers,
            dry_run=dry_run
        )
        worker.run()

        return True

    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        return False
