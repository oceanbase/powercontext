import argparse
import os
import numpy as np

from methods.add import MemoryADD
from methods.search import MemorySearch


def main():
    parser = argparse.ArgumentParser(description="Run memory experiments")
    parser.add_argument("--method", choices=["add", "search"], default="add", help="Method to use")
    parser.add_argument("--chunk_size", type=int, default=1000, help="Chunk size for processing")
    parser.add_argument("--output_folder", type=str, default="results/", help="Output path for results")
    parser.add_argument("--top_k", type=int, default=30, help="Number of top memories to retrieve")
    parser.add_argument("--filter_memories", action="store_true", default=False, help="Whether to filter memories")
    parser.add_argument("--is_graph", action="store_true", default=False, help="Whether to use graph-based search")
    parser.add_argument("--num_chunks", type=int, default=1, help="Number of chunks to process")

    args = parser.parse_args()

    # Get dataset path from environment variable, fallback to default
    dataset_path = 'dataset/locomo10.json'

    # Add your experiment logic here
    print(f"Running experiments with chunk size: {args.chunk_size}")
    print(f"Using dataset: {dataset_path}")

    if args.method == "add":
        memory_manager = MemoryADD(data_path=dataset_path, is_graph=args.is_graph)
        memory_manager.process_all_conversations()
        server_time = memory_manager.server_execution_time
        request_count = memory_manager.request_count
        request_times = memory_manager.request_times
    elif args.method == "search":
        output_file_path = os.path.join(
            args.output_folder,
            f"results.json",
        )
        memory_searcher = MemorySearch(output_file_path, args.top_k, args.filter_memories, args.is_graph)
        memory_searcher.process_data_file(dataset_path)
        server_time = memory_searcher.server_execution_time
        request_count = memory_searcher.request_count
        request_times = memory_searcher.request_times

    # Calculate P95 latency
    p95_latency = np.percentile(request_times, 95)

    with open(os.path.join(args.output_folder, "evaluation.txt"), "a") as f:
        f.write(f"action: {args.method}\n")
        f.write(f"  Total server execution time: {server_time:.4f} seconds\n")
        f.write(f"  Total requests: {request_count}\n")
        f.write(f"  Average request time: {(server_time / request_count):.4f} seconds\n")
        f.write(f"  P95 request time: {p95_latency:.4f} seconds\n")


if __name__ == "__main__":
    main()
