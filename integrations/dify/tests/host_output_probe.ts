// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Execute official output parsing/selector functions with a fixture node inventory.
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { spawnSync } from 'node:child_process'

const root = process.env.POWERCONTEXT_DIFY_SOURCE!
const read = (path: string) => readFileSync(join(root, path), 'utf8')
const stripImports = (source: string) => source.replace(/^import.*\r?\n/gm, '').replaceAll('export const ', 'const ')
const defaults = stripImports(read('web/app/components/workflow/nodes/tool/default.ts')).replace('export default nodeDefault', '')
const outputTypes = stripImports(read('web/app/components/workflow/nodes/tool/output-schema-utils.ts'))
const vars = read('web/app/components/workflow/nodes/_base/components/variable/utils.ts')
const extract = (start: string, end: string) => vars.slice(vars.indexOf(start), vars.indexOf(end)).replaceAll('export const ', 'const ')
const schemaFile = process.argv[2]!
const runner = join(dirname(schemaFile), 'dify-selectors.ts')
const preamble = `
const VarType = Object.fromEntries(['arrayString','arrayNumber','arrayBoolean','arrayObject','arrayFile','arrayAny','file','string','number','integer','boolean','object','array','any'].map(k=>[k,k]));
const getMatchedSchemaType = () => undefined;
const BlockEnum = {Tool:'tool',Start:'start',Iteration:'iteration',Loop:'loop'};
const CollectionType = {builtIn:'builtin',custom:'custom',workflow:'workflow',mcp:'mcp'};
const VarKindType = {variable:'variable'};
const TOOL_OUTPUT_STRUCT = [];
const Type = Object.fromEntries(['object','string','number','boolean','array'].map(k=>[k,k]));
const canFindTool = (a,b)=>a===b;
const genNodeMetaData = x=>x;
`
const schema = JSON.parse(readFileSync(schemaFile, 'utf8'))
const fixtures = `
const schemas = ${JSON.stringify(schema)};
const inventory = {buildInTools:[{id:'powercontext',tools:Object.entries(schemas).map(([name,output_schema])=>({name,output_schema}))}]};
const toolNode = name=>({id:name,data:{type:'tool',provider_id:'powercontext',provider_type:'builtin',tool_name:name}});
// Only node inventory and unrelated schema-matching imports are fixture data.
const toNodeOutputVars = nodes=>nodes.map(node=>({nodeId:node.id,vars:nodeDefault.getOutputVars(node.data,inventory,null)}));
const check = (condition,message)=>{if(!condition)throw Error(message)};
for (const name of Object.keys(schemas)) {
  const outputs = nodeDefault.getOutputVars(toolNode(name).data,inventory,null);
  const result = outputs.find(v=>v.variable==='result');
  check(result.type==='object' && Object.keys(result.children.schema.properties).length>0, name);
}
const selectors = [
  ['pc_prepare_context',['result','content'],'string'],
  ['pc_memory_get',['result','citation'],'object'],
  ['pc_memory_get',['result','citation','entry_id'],'string'],
  ['pc_memory_get',['result','citation','memory_ref','revision'],'number'],
  ['pc_memory_get',['result','artifact_id'],'string'],
  ['pc_memory_get',['result','content','text'],'string'],
  ['pc_memory_get',['result','etag'],'string'],
  ['pc_memory_state',['result','artifact','revision'],'number'],
  ['pc_memory_state',['result','state_version'],'number'],
  ['pc_handoff_prepare',['result','next_action'],'object'],
  ['pc_handoff_finalize',['result','base'],'object'],
  ['pc_handoff_activate',['result','draft'],'object'],
  ['pc_handoff_activate',['result','draft','objective'],'string'],
  ['pc_handoff_commit',['result','reference'],'object'],
  ['pc_experience_generate',['result','candidate'],'object'],
  ['pc_experience_generate',['result','candidate','candidate_id'],'string'],
  ['pc_experience_generate',['result','candidate','target','revision'],'number'],
  ['pc_skill_generate',['result','candidate','candidate_id'],'string'],
  ['pc_review_get',['result','permissions'],'object'],
  ['pc_review_get',['result','result_artifact','revision'],'number'],
];
for (const [name,path,expected] of selectors) {
  const type = getVarType({valueSelector:[name,...path],availableNodes:[toolNode(name)],isChatMode:false,allPluginInfoList:inventory});
  check(type===expected, name+'.'+path.join('.')+': '+type+', expected '+expected);
}
console.log(JSON.stringify({tools:Object.keys(schemas).length,selectors:selectors.length}));
`
writeFileSync(runner, preamble + outputTypes + defaults
  + extract('export const isSystemVar', 'const hasValidChildren')
  + extract('const structTypeToVarType', 'export const varTypeToStructType')
  + extract('export const getVarType', 'export const toNodeAvailableVars') + fixtures)
const run = spawnSync(process.execPath, ['--experimental-strip-types', runner], {encoding:'utf8'})
process.stdout.write(run.stdout || '')
process.stderr.write(run.stderr || '')
process.exit(run.status ?? 1)
