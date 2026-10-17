export interface NumericField {
  id: string; path: string[]; name: string; type: string; value: string;
  minimum: string; maximum: string; shortcut: string | null; editable: boolean;
  readonly_reason: string;
}
export interface SaveFile { name: string; size: number; modified_at: string }
export interface SaveSlot {
  id: number; index: number; main_file: string; home_file: string;
  status: 'ready' | 'empty' | 'missing'; missing_files: string[]; issues: string[]; related_files: string[];
  size: number | null; modified_at: string | null;
  summary: null | {story: GameplayData['story']; money: string | null;
    weapons: {index: number; name: string}[]; world_day: string | null; world_weekday: string | null;
    revenue: string | null; cumulative_days: string | null; days_note: string};
}
export interface PathSettings { save_dir: string; game_dir: string; setup_complete?: boolean | string }
export interface SetupState extends PathSettings { required: boolean; save_detected: boolean; game_detected: boolean }
export interface StoryTask {stage: string; name?: string; objective?: string; details?: string; selectable?: boolean}
export interface GameplayData {
  available: boolean; reason?: string;
  weapons: {index: number; name: string; fields: {label: string; field_id: string}[];
    skills: {id: string; name: string}[]; skills_editable: boolean; skills_reason?: string;
    zoom?: {field_id: string; value: string; editable: boolean; reason?: string}}[];
  skill_options: {id: string; name: string}[];
  inventory: {index: number; name: string; item_id: string; description: string; quantity: string; field_id: string | null}[];
  story: {available: boolean; reason?: string; stage?: string; name?: string; details?: string; objective?: string; field_id?: string; editable?: boolean; goals?: {label: string; value: string}[]};
  story_options: StoryTask[];
  statistics: {label: string; value: string; field_id?: string; note?: string}[];
}
export interface SaveDocument {
  name: string; version: string; engine: string; fields: NumericField[];
  group_version: string;
  gameplay?: GameplayData;
  opaque: {path: string[]; type: string; size: number; reason: string}[];
  comfort: {available: boolean; reason: string; value?: string; maximum?: string;
    reachable_minimum?: string; home_file?: string; placed?: string; map_total?: string;
    breakdown?: {label:string; count:number; each:string; total:string}[];
    map_breakdown?: {map:string; name:string; class:string; comfort:number}[]};
}
export interface EditPreview {
  story: null | {old: StoryTask; value: StoryTask; warning: string};
  weapon_skills: {index:number; old:string[]; value:string[]}[];
  applied: {id:string; old:string; value:string; clamped:boolean}[];
  comfort: null | {original:string; requested:string; target:string; value:string;
    clamped:boolean; rounded_down:boolean; nearest:boolean; home_file:string; mutations:number;
    placement:string; changes:{action:string; label:string; count:number; indices?:number[]}[]};
}
export interface Backup {
  id: string; name: string; created_at: string; kind: string; size: number;
  source_dir: string; files: { name: string; size: number; sha256: string }[];
}
export interface Status { game_running: boolean; pending_recovery: number; data_dir: string }
let sessionToken = ''
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch('/api' + path, {
    method, headers: {'Content-Type': 'application/json', 'X-Editor-Token': sessionToken},
    ...(body !== undefined ? {body: JSON.stringify(body)} : {})
  })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : '请求参数无效，请检查输入')
  }
  return data
}
export async function initSession() {
  sessionToken = (await api<{token: string}>('/session')).token
}
