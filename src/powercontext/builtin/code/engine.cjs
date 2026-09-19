// Copyright (c) 2026 OceanBase.
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
// http://www.apache.org/licenses/LICENSE-2.0
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// All graph access stays behind CodeGraph's public library API. No repository
// code is loaded or executed. The Python parent owns source reads and budgets.
const fs = require('node:fs');
const { CodeGraph, setLogger, silentLogger } = require(process.argv[2]);
setLogger(silentLogger);

function location(node) {
  return {
    path: node.filePath,
    qualified_name: node.qualifiedName.replaceAll('::', '.'),
    start_line: node.startLine,
    end_line: node.endLine,
  };
}

function definition(node) {
  return {
    kind: 'definition',
    path: node.filePath,
    location: location(node),
    signature: node.signature ? node.signature.slice(0, 2048) : null,
  };
}

function target(graph, operation) {
  const nodes = graph.getNodesInFile(operation.path).filter(node =>
    node.qualifiedName.replaceAll('::', '.') === operation.qualified_name &&
    node.startLine === operation.start_line
  );
  if (nodes.length !== 1) throw new Error('invalid_code_target');
  return nodes[0];
}

function ambiguous(graph, node) {
  // v1.6.0 can bind references to the wrong same-name Python definition.
  // Do not turn this known engine limitation into a false relationship.
  return graph.getNodesByName(node.name).filter(n => n.language === 'python' && n.kind === node.kind).length > 1;
}

function relations(graph, node, direction, limit) {
  if (ambiguous(graph, node)) throw new Error('unsupported_capability');
  const edges = direction === 'callees' ? graph.getOutgoingEdges(node.id) : graph.getIncomingEdges(node.id);
  const items = [];
  let omitted = 0;
  let examined = 0;
  for (const edge of edges) {
    if (++examined > 1000) return { items, omitted, truncated: true };
    if (edge.kind !== 'calls') continue;
    const source = graph.getNode(edge.source);
    const destination = graph.getNode(edge.target);
    if (!source || !destination || ambiguous(graph, source) || ambiguous(graph, destination)) {
      omitted++;
      continue;
    }
    const other = direction === 'callees' ? destination : source;
    if (other.language !== 'python') {
      omitted++;
      continue;
    }
    items.push({
      ...definition(other),
      kind: 'relationship',
      relationships: [{
        kind: 'calls',
        method: ['tree-sitter', 'scip', 'heuristic'].includes(edge.provenance) ? edge.provenance : 'unknown',
        source: location(source),
        target: location(destination),
        call_line: edge.line || null,
      }],
    });
    if (items.length >= limit) break;
  }
  return { items, omitted, truncated: items.length >= limit };
}

function isTest(node) {
  const filename = node.filePath.split('/').at(-1);
  return node.name.startsWith('test_') &&
    ['function', 'method'].includes(node.kind) &&
    (filename.startsWith('test_') || filename.endsWith('_test.py'));
}

function traverse(graph, starts, depth, limit, testsOnly) {
  const seen = new Set();
  const selected = new Set(testsOnly ? [] : starts.map(node => node.id));
  const queue = [];
  let omitted = 0;
  for (const node of starts) {
    if (seen.size >= 500) break;
    if (ambiguous(graph, node)) { omitted++; continue; }
    if (!seen.has(node.id)) {
      seen.add(node.id);
      queue.push({ node, path: [] });
    }
  }
  const items = [];
  let examined = 0;
  let truncated = starts.length > 500;
  for (let offset = 0; offset < queue.length; offset++) {
    const current = queue[offset];
    if (current.path.length >= depth) continue;
    for (const edge of graph.getIncomingEdges(current.node.id)) {
      if (++examined > 1000 || seen.size >= 500) return { items, omitted, truncated: true };
      if (edge.kind !== 'calls') continue;
      const caller = graph.getNode(edge.source);
      if (!caller || caller.language !== 'python' || ambiguous(graph, caller)) { omitted++; continue; }
      const path = [...current.path, {
        kind: 'calls',
        method: ['tree-sitter', 'scip', 'heuristic'].includes(edge.provenance) ? edge.provenance : 'unknown',
        source: location(caller),
        target: location(current.node),
        call_line: edge.line || null,
      }];
      if (!seen.has(caller.id)) {
        seen.add(caller.id);
        queue.push({ node: caller, path });
        if (path.length >= depth && graph.getIncomingEdges(caller.id).some(e => e.kind === 'calls')) truncated = true;
      }
      if (!selected.has(caller.id) && (!testsOnly || isTest(caller))) {
        selected.add(caller.id);
        items.push({ ...definition(caller), kind: testsOnly ? 'test' : 'relationship', relationships: path });
        if (items.length >= limit) return { items, omitted, truncated: true };
      }
    }
  }
  return { items, omitted, truncated };
}

function symbols(graph, operation, limit) {
  const options = { languages: ['python'], kinds: ['function', 'method', 'class', 'variable', 'constant', 'property'], limit };
  // v1.6.0 ignores includePatterns and applies even its path: query filter
  // after limiting. Search the finite index before applying our file/result
  // limits, retaining the engine's text matching and ranking semantics.
  if (operation.path) options.limit = graph.getStats().nodeCount;
  return graph.searchNodes(operation.query, options).map(result => result.node).filter(node =>
    node.kind !== 'file' && (!operation.path || node.filePath === operation.path)
  ).slice(0, limit);
}

async function main(input) {
  if (input.command === 'build') {
    const graph = await CodeGraph.init(input.root);
    try {
      const result = await graph.indexAll();
      if (!result.success || graph.getIndexState() !== 'complete') throw new Error('code_index_failed');
      return {
        files: graph.getFiles().map(file => ({ path: file.path, errors: file.errors?.length || 0 })),
        indexed_files: result.filesIndexed,
        parse_failures: result.filesErrored,
        unresolved_references: graph.getPendingReferenceCount(),
        build: graph.getIndexBuildInfo(),
      };
    } finally {
      graph.close();
    }
  }
  // The parent supplies a disposable index copy: CodeGraph's v1.6.0 public
  // open method does not enforce its documented readOnly option.
  const graph = await CodeGraph.open(input.root, { sync: false });
  try {
    const operation = input.operation;
    if (operation.kind === 'symbols') {
      const nodes = symbols(graph, operation, input.prepare ? 16 : operation.limit);
      let items = nodes.map(definition);
      let omitted = 0;
      if (input.prepare) {
        for (const node of nodes.slice(0, 4)) {
          if (ambiguous(graph, node)) {
            omitted++;
            continue;
          }
          const expanded = relations(graph, node, 'callers', 4);
          items.push(...expanded.items);
          omitted += expanded.omitted;
        }
        items = items.slice(0, 16);
      }
      return { items, omitted, truncated: nodes.length >= (input.prepare ? 16 : operation.limit) };
    }
    if (operation.kind === 'callers' || operation.kind === 'callees') {
      return relations(graph, target(graph, operation), operation.kind, operation.limit);
    }
    if (operation.kind === 'impact') {
      const node = target(graph, operation);
      if (ambiguous(graph, node)) throw new Error('unsupported_capability');
      return traverse(graph, [node], operation.depth, operation.limit, false);
    }
    if (operation.kind === 'affected_tests') {
      const starts = [];
      for (const path of operation.changed_paths) {
        const nodes = graph.getNodesInFile(path).filter(n => n.language === 'python' &&
          ['function', 'method', 'class', 'variable', 'constant', 'property'].includes(n.kind));
        if (!nodes.length) throw new Error('invalid_code_target');
        starts.push(...nodes);
      }
      return traverse(graph, starts, 5, operation.limit, true);
    }
    throw new Error('unsupported_capability');
  } finally {
    graph.close();
  }
}

const knownErrors = new Set(['invalid_code_target', 'unsupported_capability', 'code_index_failed']);
main(JSON.parse(fs.readFileSync(0, 'utf8')))
  .then(result => process.stdout.write(JSON.stringify({ result })))
  .catch(error => process.stdout.write(JSON.stringify({
    error: knownErrors.has(error.message) ? error.message : 'code_engine_failed',
  })));
