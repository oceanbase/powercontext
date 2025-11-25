/**
 * Docusaurus plugin to sort documents by numeric prefix in filename
 *
 * This plugin sorts documents in the sidebar based on numeric prefixes in filenames.
 * Files with numeric prefixes like 0000-xxx.md, 0001-xxx.md will be sorted accordingly.
 *
 * The plugin works by reading the source docs directory (before renaming) to get
 * the original filenames with numeric prefixes, then sorting sidebar items accordingly.
 *
 * Special handling:
 * - overview.md files are always sorted first (before numbered items)
 *
 * Examples:
 * - overview.md -> sorted first (position -1)
 * - 0000-intro.md -> sorted second (position 0)
 * - 0001-getting_started.md -> sorted third (position 1)
 * - other.md (no prefix) -> sorted last (position 10000+)
 */

const fs = require('fs');
const path = require('path');

/**
 * Extract numeric prefix from filename
 * @param {string} filename - The filename (with or without path)
 * @returns {number|null} - The numeric prefix, or null if not found
 */
function extractNumericPrefix(filename) {
  // Match patterns like: 0000-xxx.md, 0001-xxx.md
  const basename = path.basename(filename, '.md');
  const prefixPattern = /^(\d+)-(.+)$/;
  const match = basename.match(prefixPattern);

  if (match) {
    return parseInt(match[1], 10);
  }

  return null;
}

/**
 * Extract numeric prefix from document ID or filename
 * Since files are no longer renamed, we can extract prefix directly from the docId
 * or from the actual filename in the docs directory
 * @param {string} docId - The document ID (e.g., 'guides/0001-getting_started' or 'guides/getting_started')
 * @param {string} docsPath - Path to the docs directory
 * @returns {number|null} - The numeric prefix, or null if not found
 */
function getNumericPrefixFromDocId(docId, docsPath) {
  // First, try to extract prefix directly from docId (if it contains the prefix)
  const parts = docId.split('/');
  const lastPart = parts[parts.length - 1];
  const prefixPattern = /^(\d+)-(.+)$/;
  const match = lastPart.match(prefixPattern);
  if (match) {
    return parseInt(match[1], 10);
  }

  // If docId doesn't have prefix, look for the actual file in the directory
  const docPath = path.join(docsPath, docId);
  const dir = path.dirname(docPath);
  const baseName = path.basename(docPath);

  if (!fs.existsSync(dir)) {
    return null;
  }

  // Look for files with numeric prefix that match the base name
  const files = fs.readdirSync(dir);
  for (const file of files) {
    if (file.endsWith('.md')) {
      const fileMatch = file.match(prefixPattern);
      if (fileMatch) {
        // Check if the part after prefix matches the document name
        const nameAfterPrefix = fileMatch[2].replace(/\.md$/, '');
        if (nameAfterPrefix === baseName) {
          return parseInt(fileMatch[1], 10);
        }
      }
    }
  }

  return null;
}

/**
 * Custom sidebar items generator that sorts by numeric prefix
 * This should be used in docusaurus.config.ts as sidebarItemsGenerator
 */
async function sidebarItemsGenerator(args) {
  const {defaultSidebarItemsGenerator, item, ...restArgs} = args;
  // Since files are no longer renamed, we can read directly from args.docsDir
  // or use the docs directory path
  let docsPath;

  if (args.docsDir) {
    docsPath = args.docsDir;
  } else {
    // Fallback: try to find docs directory
    docsPath = path.resolve(process.cwd(), 'docs');
    if (!fs.existsSync(docsPath)) {
      docsPath = path.resolve(__dirname, '../../docs');
    }
  }

  if (!fs.existsSync(docsPath)) {
    console.warn(`[docusaurus-plugin-sort-docs] Docs path not found: ${docsPath}`);
    console.warn(`[docusaurus-plugin-sort-docs] args.docsDir: ${args.docsDir}`);
    console.warn(`[docusaurus-plugin-sort-docs] process.cwd(): ${process.cwd()}`);
  } else {
    console.log(`[docusaurus-plugin-sort-docs] Using docs path: ${docsPath}`);
  }

  // Call the default generator and handle both sync and async returns
  const sidebarItems = await Promise.resolve(defaultSidebarItemsGenerator(args));

  /**
   * Sort sidebar items based on numeric prefix
   * Items with numeric prefixes are sorted by prefix number
   * Items without numeric prefixes are sorted alphabetically after numbered items
   * overview.md files are always sorted first (before numbered items)
   */
  function sortSidebarItems(items) {
    // First, map items and calculate sort keys
    const itemsWithKeys = items.map(item => {
      let sortKey = null;
      let sortName = ''; // For alphabetical sorting of items without prefix

      if (item.type === 'doc') {
        const docId = item.id;
        // Extract the filename to check if it's overview
        const parts = docId.split('/');
        const filename = parts[parts.length - 1].toLowerCase();
        
        // Check if this is an overview.md file (case-insensitive)
        if (filename === 'overview') {
          // overview.md files should be sorted first (before numbered items)
          sortKey = -1;
          sortName = 'overview';
          console.log(`[docusaurus-plugin-sort-docs] Doc ${docId} is overview, setting sortKey to -1`);
        } else {
          // Get numeric prefix from docId or filename
          const numericPrefix = getNumericPrefixFromDocId(docId, docsPath);
          if (numericPrefix !== null) {
            sortKey = numericPrefix;
            // Debug log (can be removed later)
            console.log(`[docusaurus-plugin-sort-docs] Doc ${docId} has prefix ${numericPrefix}`);
          } else {
            // No prefix: use a large base number (10000) + alphabetical order
            // Extract the filename for alphabetical sorting
            sortName = filename;
            sortKey = 10000; // Base value for items without prefix
          }
        }
      } else if (item.type === 'category' && item.items) {
        // Recursively sort category items
        item.items = sortSidebarItems(item.items);
        // For categories, use the minimum sort key of their items
        if (item.items.length > 0) {
          const firstItem = item.items[0];
          // Try to get sort key from first item (if it's a doc)
          if (firstItem.type === 'doc') {
            const firstDocId = firstItem.id;
            const firstParts = firstDocId.split('/');
            const firstFilename = firstParts[firstParts.length - 1].toLowerCase();
            
            // Check if first item is overview
            if (firstFilename === 'overview') {
              sortKey = -1;
              sortName = 'overview';
            } else {
              const firstPrefix = getNumericPrefixFromDocId(firstDocId, docsPath);
              if (firstPrefix !== null) {
                sortKey = firstPrefix;
              } else {
                sortName = firstFilename;
                sortKey = 10000;
              }
            }
          }
        } else {
          sortKey = 10000;
        }
      }

      return { item, sortKey, sortName };
    });

    // Debug: log before sorting
    console.log(`[docusaurus-plugin-sort-docs] Sorting ${itemsWithKeys.length} items`);
    itemsWithKeys.forEach(({ item, sortKey, sortName }) => {
      if (item.type === 'doc') {
        const keyDisplay = sortKey < 10000 ? `sortKey=${sortKey}` : `sortKey=${sortKey} (alphabetical: ${sortName})`;
        console.log(`  - ${item.id}: ${keyDisplay}`);
      }
    });

    // Sort by sort key, then by alphabetical order for items without prefix
    itemsWithKeys.sort((a, b) => {
      // First compare by sort key
      // overview.md files (sortKey = -1) will be sorted first
      if (a.sortKey !== b.sortKey) {
        return a.sortKey - b.sortKey;
      }
      // If sort keys are equal (both >= 10000, meaning no prefix), sort alphabetically
      if (a.sortKey >= 10000 && b.sortKey >= 10000) {
        return a.sortName.localeCompare(b.sortName);
      }
      // If both are overview (sortKey = -1), maintain original order
      return 0;
    });

    // Debug: log after sorting
    console.log(`[docusaurus-plugin-sort-docs] After sorting:`);
    itemsWithKeys.forEach(({ item, sortKey, sortName }, index) => {
      if (item.type === 'doc') {
        const keyDisplay = sortKey < 10000 ? `sortKey=${sortKey}` : `sortKey=${sortKey} (alphabetical: ${sortName})`;
        console.log(`  ${index + 1}. ${item.id}: ${keyDisplay}`);
      }
    });

    // Return items without the sortKey property
    return itemsWithKeys.map(({ item }) => item);
  }

  return sortSidebarItems(sidebarItems);
}

module.exports = sidebarItemsGenerator;

