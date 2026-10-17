<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import GameplayPanel from './GameplayPanel.vue'
import { weaponPosition } from './labels'
import { Archive, ArrowUpRight, Check, ChevronLeft, ChevronRight, Coins, Database, FolderOpen, HardDrive, History, LayoutDashboard, LoaderCircle, Menu, Pencil, Plus, Power, RefreshCw, Save, Search, Settings, ShieldCheck, SlidersHorizontal, Trash2, X } from 'lucide-vue-next'
import { api, initSession, type Backup, type EditPreview, type NumericField, type PathSettings, type SaveDocument, type SaveSlot, type SetupState, type Status } from './api'

type Tab = 'editor' | 'advanced' | 'backups' | 'history' | 'settings'
const tab = ref<Tab>('editor')
const navOpen = ref(false)
const compact = ref(false)
const navigation = [
  {id: 'editor' as const, label: '存档编辑', icon: LayoutDashboard},
  {id: 'advanced' as const, label: '高级参数', icon: SlidersHorizontal},
  {id: 'backups' as const, label: '备份仓库', icon: Archive},
  {id: 'history' as const, label: '操作记录', icon: History},
  {id: 'settings' as const, label: '路径设置', icon: Settings},
]
let layoutQuery: MediaQueryList | undefined
function updateLayout() { compact.value = layoutQuery?.matches ?? false; navOpen.value = false }
function closeNavigation() { navOpen.value = false; void nextTick(() => document.querySelector<HTMLButtonElement>('.nav-toggle')?.focus()) }
const status = ref<Status>({game_running: false, pending_recovery: 0, data_dir: ''})
const settings = ref<PathSettings>({save_dir: '', game_dir: ''})
const setupRequired = ref(false)
const setupError = ref('')
const detecting = ref(false)
const detectedPaths = ref({save_detected: false, game_detected: false})
const slots = ref<SaveSlot[]>([])
const selectedSlot = ref(0)
const currentSlot = computed(() => slots.value.find(slot => slot.id === selectedSlot.value))
const selected = ref('')
const doc = ref<SaveDocument | null>(null)
const changes = ref<Record<string, string>>({})
const weaponSkills = ref<Record<string, string[]>>({})
const editPreview = ref<EditPreview | null>(null)
const query = ref('')
const group = ref('')
const page = ref(1)
const backups = ref<Backup[]>([])
const backupQuery = ref('')
const logs = ref<{id: string; kind: string; status: string; created_at: string; details: Record<string, unknown>}[]>([])
const busy = ref(false)
const ready = ref(false)
const stopped = ref(false)
const notice = ref<{text: string; error: boolean} | null>(null)
const modal = ref<{type: 'create' | 'rename' | 'restore' | 'delete' | 'save' | 'switch' | 'exit'; backup?: Backup; target?: string} | null>(null)
const backupName = ref('')
let timer: ReturnType<typeof setInterval> | undefined
const readonly = computed(() => busy.value || !ready.value || setupRequired.value || status.value.game_running || status.value.pending_recovery > 0)
const money = computed(() => doc.value?.fields.find(f => f.shortcut === 'money'))
const changedCount = computed(() => changedFields.value.length + Object.keys(weaponSkills.value).length)
const editorFields = computed(() => (doc.value?.fields ?? []).filter(f => f.shortcut !== 'comfort' && !f.path.some(p => /舒适度|舒适值/.test(p))))
const groups = computed(() => [...new Set(editorFields.value.map(f => f.path[0]))])
const filtered = computed(() => editorFields.value.filter(f => (!group.value || f.path[0] === group.value) && f.path.join(' / ').toLowerCase().includes(query.value.toLowerCase())))
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / 60)))
const visible = computed(() => filtered.value.slice((page.value - 1) * 60, page.value * 60))
const changedFields = computed(() => (doc.value?.fields ?? []).filter(f => f.id in changes.value))
const visibleBackups = computed(() => backups.value.filter(b => b.name.toLowerCase().includes(backupQuery.value.toLowerCase())))
function date(value: string) { return new Date(value).toLocaleString('zh-CN', {hour12: false}) }
function bytes(value: number) { return value < 1024 ? value + ' B' : value < 1024*1024 ? (value/1024).toFixed(1)+' KB' : (value/1024/1024).toFixed(1)+' MB' }
function label(field: NumericField) { return field.path.map(p => p.replace(/_\d+_[A-F0-9]{32}$/i, '')).join(' / ') }
function display(value: string) {
  if (/^-?\d+$/.test(value)) return value.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return value
}
function update(field: NumericField, value: string) {
  if (value === field.value) delete changes.value[field.id]
  else changes.value[field.id] = value
}
function updateSkills(index: number, values: string[]) {
  const original = doc.value?.gameplay?.weapons.find(weapon => weapon.index === index)?.skills.map(skill => skill.id) ?? []
  if (JSON.stringify(values) === JSON.stringify(original)) delete weaponSkills.value[String(index)]
  else weaponSkills.value[String(index)] = values
}
function skillName(id: string) { return doc.value?.gameplay?.skill_options.find(option => option.id === id)?.name ?? id }
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  notice.value = null
  try { await action() } catch (error) { notice.value = {text: error instanceof Error ? error.message : '操作失败', error: true} }
  finally { busy.value = false; await refreshStatus().catch(() => {}) }
}
async function refreshStatus() { if (!stopped.value) status.value = await api<Status>('/status') }
async function loadSave(name: string) {
  const next = await api<SaveDocument>('/saves/' + encodeURIComponent(name))
  selected.value = name; doc.value = next; changes.value = {}; weaponSkills.value = {}; query.value = ''; group.value = ''; page.value = 1
  editPreview.value = null
}
async function refreshFiles() {
  const result = await api<{slots: SaveSlot[]; selected: number}>('/saves/slots')
  slots.value = result.slots
  await loadSlot(slots.value.some(slot => slot.id === selectedSlot.value) ? selectedSlot.value : result.selected)
}
function slotStatus(slot: SaveSlot) {
  return {ready: '已有存档', empty: '无存档', missing: '无存档'}[slot.status]
}
async function loadSlot(id: number) {
  selectedSlot.value = id
  selected.value = ''; doc.value = null; resetChanges()
  const slot = currentSlot.value
  if (slot?.status === 'ready') await loadSave(slot.main_file)
}
async function loadBackups() { backups.value = await api<Backup[]>('/backups') }
async function switchTab(value: Tab) {
  if (setupRequired.value) return
  tab.value = value
  if (compact.value && navOpen.value) closeNavigation()
  else navOpen.value = false
  if (value === 'backups') await run(loadBackups)
  if (value === 'history') await run(async () => { logs.value = await api('/history') })
}
async function detectPaths() {
  detecting.value = true
  setupError.value = ''
  try {
    const detected = await api<SetupState>('/setup')
    settings.value = {...settings.value, save_dir: detected.save_dir, game_dir: detected.game_dir}
    detectedPaths.value = detected
  } catch (error) { setupError.value = error instanceof Error ? error.message : '自动检测失败，请手动填写目录。' }
  finally { detecting.value = false }
}
async function savePaths(firstRun = false) {
  await run(async () => {
    setupError.value = ''
    try {
      settings.value = await api<PathSettings>(firstRun ? '/setup' : '/settings', firstRun ? 'POST' : 'PUT', {save_dir: settings.value.save_dir, game_dir: settings.value.game_dir})
    } catch (error) {
      if (firstRun) setupError.value = error instanceof Error ? error.message : '目录验证失败，请检查路径。'
      else throw error
      return
    }
    setupRequired.value = false
    ready.value = true
    await refreshFiles()
    notice.value = {text: '路径设置已保存。', error: false}
  })
}
function chooseSlot(event: Event) {
  const input = event.target as HTMLSelectElement
  const id = Number(input.value)
  input.value = String(selectedSlot.value)
  if (changedCount.value) modal.value = {type: 'switch', target: String(id)}
  else void run(() => loadSlot(id))
}
function newBackup() { backupName.value = '我的存档 · ' + new Date().toLocaleString('zh-CN'); modal.value = {type: 'create'} }
function editPayload() {
  return {version:doc.value?.version, group_version:doc.value?.group_version,
    changes:changes.value, ...(Object.keys(weaponSkills.value).length ? {weapon_skills: weaponSkills.value} : {})}
}
async function previewSave() {
  focusBeforeDialog = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await run(async () => {
    editPreview.value = await api<EditPreview>('/saves/' + encodeURIComponent(selected.value) + '/preview', 'POST', editPayload())
    modal.value = {type:'save'}
  })
}
function resetChanges() { changes.value = {}; weaponSkills.value = {}; editPreview.value = null }
async function confirmModal() {
  const action = modal.value
  if (!action) return
  await run(async () => {
    if (action.type === 'create') {
      await api('/backups', 'POST', {name: backupName.value}); await loadBackups()
      notice.value = {text: '完整存档已备份，存档内容保持原样。', error: false}
    } else if (action.type === 'rename' && action.backup) {
      await api('/backups/' + action.backup.id, 'PATCH', {name: backupName.value}); await loadBackups()
      notice.value = {text: '备份名称已更新。', error: false}
    } else if (action.type === 'delete' && action.backup) {
      await api('/backups/' + action.backup.id, 'DELETE'); await loadBackups()
      notice.value = {text: '备份已删除。', error: false}
    } else if (action.type === 'restore' && action.backup) {
      await api('/backups/' + action.backup.id + '/restore', 'POST'); await refreshFiles(); await loadBackups()
      notice.value = {text: '备份已加载，加载前的存档已自动保留为恢复点。', error: false}
    } else if (action.type === 'save') {
      if (!doc.value) return
      const result = await api<EditPreview>('/saves/' + encodeURIComponent(selected.value) + '/edit', 'POST', editPayload())
      await refreshFiles()
      const clamped = result.applied.filter(x => x.clamped)
      notice.value = {text: clamped.length ? '已保存。自动修正：' + clamped.map(x => x.id + ' → ' + x.value).join('；') : '修改已保存，修改前的完整存档已自动备份。', error: false}
    } else if (action.type === 'switch' && action.target) {
      selectedSlot.value = Number(action.target)
      await refreshFiles()
    }
    else if (action.type === 'exit') {
      await api('/shutdown', 'POST'); stopped.value = true; ready.value = false
      if (timer) clearInterval(timer)
      notice.value = {text: '修改器已退出，可以关闭此网页。重新使用时双击 EXE。', error: false}
    }
    modal.value = null
  })
}
let focusBeforeDialog: HTMLElement | null = null
watch(() => Boolean(modal.value || setupRequired.value), async (open) => {
  if (open && document.activeElement instanceof HTMLElement && document.activeElement !== document.body) focusBeforeDialog = document.activeElement
  await nextTick()
  if (open) (document.querySelector<HTMLElement>('.modal input:not(:disabled), .modal button:not(:disabled)') ?? document.querySelector<HTMLElement>('.modal'))?.focus()
  else if (focusBeforeDialog?.isConnected) focusBeforeDialog.focus()
})
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (modal.value && !busy.value) { event.preventDefault(); modal.value = null }
    else if (navOpen.value) { event.preventDefault(); closeNavigation() }
  }
  if (event.key !== 'Tab') return
  const container = document.querySelector<HTMLElement>('.modal') ?? (compact.value && navOpen.value ? document.querySelector<HTMLElement>('.sidebar') : null)
  if (!container) return
  const controls = Array.from(container.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]')).filter(el => el.getClientRects().length)
  const first = controls[0], last = controls[controls.length-1]
  if (!first || !last) return
  if (event.shiftKey && (document.activeElement === first || !container.contains(document.activeElement))) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && (document.activeElement === last || !container.contains(document.activeElement))) { event.preventDefault(); first.focus() }
}
watch(navOpen, async (open) => {
  if (open && compact.value) { await nextTick(); document.querySelector<HTMLElement>('.sidebar a[aria-current="page"]')?.focus() }
})
onMounted(async () => {
  layoutQuery = window.matchMedia('(max-width: 760px)')
  updateLayout()
  layoutQuery.addEventListener('change', updateLayout)
  document.addEventListener('keydown', onKeydown)
  await run(async () => {
    await initSession(); await refreshStatus(); settings.value = await api<PathSettings>('/settings')
    setupRequired.value = ![true, 'true', '1'].includes(settings.value.setup_complete ?? false)
    if (setupRequired.value) await detectPaths()
    else { ready.value = true; await refreshFiles() }
  })
  timer = setInterval(() => { void refreshStatus().catch(() => {}) }, 5000)
})
onUnmounted(() => { if (timer) clearInterval(timer); layoutQuery?.removeEventListener('change', updateLayout); document.removeEventListener('keydown', onKeydown) })
</script>

<template>
  <div class="workspace aliyun-console">
    <header class="app-header" :inert="setupRequired || !!modal">
      <button class="icon-button nav-toggle" :aria-expanded="navOpen" aria-controls="product-nav" :aria-label="navOpen ? '收起导航' : '展开导航'" @click="navOpen ? closeNavigation() : navOpen = true"><Menu :size="19" /></button>
      <a class="brand" href="#editor" @click.prevent="switchTab('editor')"><SlidersHorizontal :size="21" /><span>ARKPARK 2</span></a>
      <span class="header-product">存档修改器</span>
      <div class="connection" role="status"><span :class="['status-dot', {warning: status.game_running || status.pending_recovery || stopped}]"></span>{{ stopped ? '服务已退出' : status.game_running ? '游戏运行中 · 只读' : status.pending_recovery ? '等待恢复' : '本机服务 · 已连接' }}</div>
      <span class="header-version">v0.5.4</span>
    </header>
    <button v-if="navOpen && compact" class="nav-backdrop" aria-label="关闭导航" tabindex="-1" @click="closeNavigation"></button>
    <aside id="product-nav" :class="['sidebar', {open:navOpen}]" :inert="setupRequired || !!modal || (compact && !navOpen)">
      <div class="sidebar-product"><Database :size="17" />存档管理<button class="icon-button sidebar-close" aria-label="关闭导航" @click="closeNavigation"><X :size="18" /></button></div>
      <div class="sidebar-caption">工作空间</div>
      <nav aria-label="产品导航">
        <a v-for="item in navigation" :key="item.id" :href="'#' + item.id" :class="{active:tab === item.id}" :aria-current="tab === item.id ? 'page' : undefined" @click.prevent="switchTab(item.id)"><component :is="item.icon" :size="16" />{{ item.label }}</a>
      </nav>
      <div class="sidebar-bottom"><div class="local-badge"><HardDrive :size="14" />本地存储</div><p>备份与操作记录保存在本机。</p><button class="button text" :disabled="busy || stopped" @click="modal = {type:'exit'}"><Power :size="15" />退出修改器</button></div>
    </aside>

    <main :inert="setupRequired || !!modal || (compact && navOpen)" :aria-busy="busy">
      <div class="breadcrumb" aria-label="当前位置">工作空间 <ChevronRight :size="13" /><span>{{ navigation.find(item => item.id === tab)?.label }}</span></div>
      <div class="content">
        <div v-if="notice" :class="['notice', {error: notice.error}]" role="alert"><span>{{ notice.text }}</span><button @click="notice = null" aria-label="关闭消息"><X :size="16" /></button></div>
        <div v-if="status.game_running" class="notice warning-notice">ArkPark2 正在运行。退出游戏后即可修改、备份或加载存档。</div>
        <div v-if="status.pending_recovery" class="notice warning-notice"><span>检测到未完成的写入操作，恢复点已保留。</span><button class="button secondary" :disabled="busy || status.game_running" @click="run(async () => { const r = await api<{pending:number}>('/recovery', 'POST'); if(r.pending) throw new Error('恢复尚未完成，请检查存档目录及文件占用'); await refreshFiles(); notice = {text:'原始存档已恢复。',error:false} })">恢复原始存档</button></div>

        <div v-if="busy && !doc && !currentSlot && (tab === 'editor' || tab === 'advanced')" class="loading-state" role="status"><LoaderCircle :size="18" class="spinning" />正在读取存档…</div>
        <template v-if="tab === 'editor' || tab === 'advanced'">
          <div class="page-heading"><div><h1>{{ tab === 'advanced' ? '高级参数' : '存档编辑' }}</h1><p>{{ tab === 'advanced' ? '搜索和编辑当前槽位的已解析数值，与存档编辑页共享待保存修改。' : '选择存档槽位，查看并修改已保存的游戏参数。' }}</p></div><button class="button secondary" :disabled="readonly" @click="newBackup"><Plus :size="17" />新建备份</button></div>
          <section class="save-selector"><div class="file-icon"><FolderOpen :size="24" /></div><div class="save-identity"><label for="save-slot">当前存档槽位</label><select id="save-slot" :value="selectedSlot" :disabled="busy" @change="chooseSlot"><option v-for="slot in slots" :key="slot.id" :value="slot.id">槽位 {{ slot.id }} · {{ slotStatus(slot) }}</option></select></div><div class="file-meta" v-if="currentSlot?.modified_at"><span>主存档 {{ bytes(currentSlot.size ?? 0) }}</span><span>更新于 {{ date(currentSlot.modified_at) }}</span></div><button class="icon-button" aria-label="重新读取存档" title="重新读取存档" :disabled="busy" @click="changedCount ? modal = {type:'switch',target:String(selectedSlot)} : run(refreshFiles)"><RefreshCw :size="18" :class="{spinning:busy}" /></button></section>

          <section v-if="currentSlot && (tab === 'editor' || currentSlot.status !== 'ready')" class="slot-overview panel" aria-label="槽位摘要">
            <div class="slot-heading"><h2>槽位 {{ currentSlot.id }} <span class="chip">{{ slotStatus(currentSlot) }}</span></h2><span>主进度与家园文件配对读取</span></div>
            <template v-if="currentSlot.summary">
              <div class="slot-story"><span class="slot-label">剧情进度<span v-if="currentSlot.summary.story.stage !== undefined"> · 阶段 {{ currentSlot.summary.story.stage }}</span></span><h3>{{ currentSlot.summary.story.stage === undefined ? '未记录' : currentSlot.summary.story.name || '暂无法识别任务标题' }}</h3><p>{{ currentSlot.summary.story.objective || currentSlot.summary.story.reason || '未记录任务目标' }}</p></div>
              <dl class="slot-metrics">
                <div><dt>世界岛日期</dt><dd>{{ currentSlot.summary.world_day === null ? '未记录' : '第 ' + currentSlot.summary.world_day + ' 天' }}</dd></div>
                <div><dt>货币</dt><dd>{{ currentSlot.summary.money === null ? '无法读取' : display(currentSlot.summary.money) }}</dd></div>
                <div><dt>营业额</dt><dd>{{ currentSlot.summary.revenue === null ? '未记录' : display(currentSlot.summary.revenue) }}</dd></div>
                <div v-for="weapon in currentSlot.summary.weapons" :key="weapon.index"><dt>{{ weaponPosition(weapon.index) }}</dt><dd>{{ weapon.name }}</dd></div>
              </dl>
            </template>
            <div v-if="currentSlot.status !== 'ready'" class="slot-empty" role="status"><strong>无存档</strong><p>{{ currentSlot.status === 'empty' ? '该槽位尚无正常命名的存档文件。请先在游戏中建立存档，再重新读取。' : '为避免混用进度，文件完整且可解析后才开放此槽位编辑。' }}</p><p v-if="currentSlot.missing_files.length">缺少：{{ currentSlot.missing_files.join('、') }}</p><p v-for="issue in currentSlot.issues" :key="issue">{{ issue }}</p><small>对应文件：{{ currentSlot.main_file }} + {{ currentSlot.home_file }}</small></div>
          </section>

          <template v-if="doc">
          <template v-if="tab === 'editor'">
          <div class="quick-grid">
            <section class="metric-card"><div class="metric-top"><span><Coins :size="20" />游戏货币</span><span class="chip">可编辑</span></div><div v-if="money" class="metric-value">{{ display(money.value) }}<span>余额</span></div><div v-else class="metric-unavailable">当前存档无货币字段</div><div class="metric-edit" v-if="money"><label :for="'quick-money'">目标数值</label><input id="quick-money" type="text" inputmode="decimal" :value="changes[money.id] ?? money.value" :disabled="readonly" @input="update(money, ($event.target as HTMLInputElement).value)"><span class="metric-limit">范围 0 — {{ display(money.maximum) }}</span></div><p class="metric-footnote">超出安全范围时自动修正。</p></section>
          </div>

          <GameplayPanel v-if="doc?.gameplay" :data="doc.gameplay" :fields="doc.fields" :changes="changes" :skills="weaponSkills" :readonly="readonly" @update="update" @skills="updateSkills" />

          </template>
          <template v-if="tab === 'advanced'">
          <div v-if="doc?.fields.some(f=>f.readonly_reason)" class="notice warning-notice">{{ doc.fields.find(f=>f.readonly_reason)?.readonly_reason }}</div><section class="parameters panel"><div class="panel-heading"><div><h2><SlidersHorizontal :size="19" />高级参数<span class="count">{{ editorFields.length }}</span></h2><p>浏览并编辑已解析的数值，保留其他存档内容。</p></div></div><div class="table-toolbar"><label class="searchbox"><span class="search-label">搜索参数</span><Search :size="15" /><input v-model="query" placeholder="参数名称或路径" @input="page = 1" /></label><select v-model="group" @change="page = 1" aria-label="参数分类"><option value="">全部分类</option><option v-for="name in groups" :key="name">{{ name }}</option></select><span class="results-count">{{ filtered.length }} 个参数</span></div>
            <div class="table-scroll"><table><thead><tr><th>参数 / 路径</th><th>原始值</th><th>修改值</th><th>类型 / 允许范围</th></tr></thead><tbody><tr v-for="field in visible" :key="field.id" :class="{changed:field.id in changes}"><td class="field-path" :title="field.path.join(' / ')"><span>{{ label(field) }}</span><span v-if="field.id in changes" class="changed-dot"></span></td><td class="mono original">{{ field.value }}</td><td><input class="value-input mono" type="text" inputmode="decimal" :disabled="readonly || !field.editable" :title="field.readonly_reason" :value="changes[field.id] ?? field.value" @input="update(field, ($event.target as HTMLInputElement).value)" :aria-label="label(field) + ' 修改值'" /></td><td><span class="type-label">{{ field.type.replace('Property','') }}</span><div class="range mono">{{ field.minimum }} → {{ field.maximum }}</div></td></tr><tr v-if="!visible.length"><td colspan="4" class="empty">{{ busy ? '正在读取存档…' : '没有符合条件的参数' }}</td></tr></tbody></table></div><div class="table-footer"><span>未识别的 {{ doc?.opaque.length ?? 0 }} 处结构保留原样，不开放编辑。</span><div class="pagination"><button class="icon-button" :disabled="page <= 1" @click="page--" aria-label="上一页"><ChevronLeft :size="17" /></button>{{ page }} / {{ pages }}<button class="icon-button" :disabled="page >= pages" @click="page++" aria-label="下一页"><ChevronRight :size="17" /></button></div></div>
          </section>
          </template>
          <div class="save-bar"><div><ShieldCheck :size="20" /><span>保存前自动创建完整恢复点<span class="save-count" v-if="changedCount"> · {{ changedCount }} 项待保存</span></span></div><div class="save-actions"><button class="button text" :disabled="busy || !changedCount" @click="resetChanges">撤销修改</button><button class="button primary" :disabled="readonly || !changedCount" @click="previewSave"><Save :size="17" />保存修改</button></div></div>
          </template>
        </template>

        <template v-if="tab === 'backups'">
          <div class="page-heading"><div><h1>备份仓库</h1><p>管理完整存档备份与自动恢复点，支持命名、搜索和加载。</p></div><button class="button primary" :disabled="readonly" @click="newBackup"><Plus :size="17" />新建备份</button></div>
          <div class="backup-summary"><div><Archive :size="21" /><strong>{{ backups.length }}</strong><span>份完整备份</span></div><div><Database :size="20" /><strong>{{ bytes(backups.reduce((sum,b)=>sum+b.size,0)) }}</strong><span>存档容量</span></div><div><ShieldCheck :size="20" /><span>手动备份与自动恢复点均永久保留</span></div></div>
          <label class="searchbox backup-search"><span class="search-label">备份名称</span><Search :size="15" /><input v-model="backupQuery" placeholder="输入名称搜索" /></label>
          <div class="backup-grid"><article class="backup-card" v-for="backup in visibleBackups" :key="backup.id"><div class="backup-card-top"><div class="backup-icon"><Archive :size="21" /></div><span :class="['chip', {muted: backup.kind === 'automatic'}]">{{ backup.kind === 'automatic' ? '自动恢复点' : '手动备份' }}</span></div><h3>{{ backup.name }}</h3><p>{{ date(backup.created_at) }}</p><div class="backup-info"><span>{{ backup.files.length }} 个文件</span><span>{{ bytes(backup.size) }}</span></div><details><summary>文件清单</summary><div class="backup-file" v-for="file in backup.files" :key="file.name">{{ file.name }} <span>{{ bytes(file.size) }}</span></div></details><div class="backup-actions"><button class="button secondary" :disabled="readonly" @click="modal = {type:'restore',backup}">加载备份<ArrowUpRight :size="15" /></button><div><button class="icon-button" :disabled="busy" title="重命名" aria-label="重命名备份" @click="backupName = backup.name; modal = {type:'rename',backup}"><Pencil :size="16" /></button><button class="icon-button danger" :disabled="busy" title="删除" aria-label="删除备份" @click="modal = {type:'delete',backup}"><Trash2 :size="16" /></button></div></div></article></div><div class="empty panel" v-if="!visibleBackups.length"><Archive :size="35" /><h3>{{ backupQuery ? '没有找到备份' : '从第一份备份开始' }}</h3><p>备份名称不会写入或改变游戏存档。</p></div>
        </template>

        <template v-if="tab === 'settings'">
          <div class="page-heading"><div><h1>路径设置</h1><p>连接游戏存档，所有数据保存在本地。</p></div></div><section class="panel settings-panel"><h2><FolderOpen :size="20" />游戏目录</h2><label for="save-dir">存档目录</label><input id="save-dir" v-model="settings.save_dir" /><p>读取和覆盖此目录中的 .sav 文件。</p><label for="game-dir">游戏安装目录</label><input id="game-dir" v-model="settings.game_dir" /><p>ArkPark2 的安装位置。</p><button class="button primary" :disabled="busy || !ready" @click="savePaths()"><Check :size="17" />保存设置</button></section><section class="panel settings-panel"><h2><Database :size="20" />修改器数据</h2><h3 class="field-label">备份与数据库位置</h3><div class="path-display mono">{{ status.data_dir }}</div><p>将 EXE 与 data 文件夹一起移动即可保留备份、名称和记录。</p></section>
        </template>

        <template v-if="tab === 'history'">
          <div class="page-heading"><div><h1>操作记录</h1><p>查看最近 100 次备份、修改与恢复操作。</p></div></div><section class="panel"><div class="table-scroll"><table><thead><tr><th>时间</th><th>操作</th><th>状态</th><th>详情</th></tr></thead><tbody><tr v-for="log in logs" :key="log.id"><td>{{ date(log.created_at) }}</td><td>{{ ({edit:'参数修改',restore:'加载备份',backup:'创建备份',delete_backup:'删除备份'} as Record<string,string>)[log.kind] ?? log.kind }}</td><td><span class="chip">{{ ({complete:'已完成',rolled_back:'已回滚',applying:'待恢复',aborted:'已取消'} as Record<string,string>)[log.status] ?? log.status }}</span></td><td><details><summary>{{ log.details.file ?? '操作详情' }}</summary><pre>{{ JSON.stringify(log.details,null,2) }}</pre></details></td></tr><tr v-if="!logs.length"><td colspan="4" class="empty">还没有操作记录。</td></tr></tbody></table></div></section>
        </template>
        <footer class="page-footer"><span>ARKPARK 2 · 本地存档工具</span><span><ShieldCheck :size="13" /> 自动备份保护每一次修改</span></footer>
      </div>
    </main>

    <div v-if="setupRequired" class="modal-overlay setup-overlay">
      <form class="modal setup-modal" role="dialog" tabindex="-1" aria-modal="true" aria-labelledby="setup-title" @submit.prevent="savePaths(true)">
        <div class="modal-header"><h2 id="setup-title"><FolderOpen :size="21" />首次使用 · 确认游戏路径</h2></div>
        <p>已尝试检测本机路径。请确认或修改下面的两个目录，确认后即可使用修改器。</p>
        <div v-if="setupError" class="notice error" role="alert">{{ setupError }}</div>
        <label for="setup-game">游戏安装目录 <span class="path-status">{{ detectedPaths.game_detected ? '已检测到' : '请检查或手动填写' }}</span></label>
        <input id="setup-game" v-model="settings.game_dir" required :disabled="busy || detecting" placeholder="例如 D:\SteamLibrary\steamapps\common\ArkPark2" autocomplete="off" autofocus />
        <label for="setup-save">游戏存档目录 <span class="path-status">{{ detectedPaths.save_detected ? '已检测到' : '请检查或手动填写' }}</span></label>
        <input id="setup-save" v-model="settings.save_dir" required :disabled="busy || detecting" placeholder="例如 C:\Users\你的用户名\AppData\Local\ArkPark2\Saved\SaveGames" autocomplete="off" />
        <p class="setup-hint">如未找到存档，请先运行游戏并保存一次。路径可以稍后在“路径设置”中修改。</p>
        <div class="modal-actions"><button type="button" class="button secondary" :disabled="busy || detecting" @click="detectPaths"><RefreshCw :size="16" :class="{spinning: detecting}" />{{ detecting ? '正在检测…' : '重新检测' }}</button><button type="submit" class="button primary" :disabled="busy || detecting || !settings.game_dir.trim() || !settings.save_dir.trim()"><LoaderCircle v-if="busy" :size="17" class="spinning" />确认并继续</button></div>
      </form>
    </div>

    <div v-if="modal" class="modal-overlay" @click.self="!busy && (modal = null)"><section class="modal" role="dialog" tabindex="-1" aria-modal="true" aria-labelledby="modal-title"><div class="modal-header"><h2 id="modal-title">{{ {create:'创建完整备份',rename:'重命名备份',restore:'加载这份备份？',delete:'删除这份备份？',save:'确认保存修改',switch:'放弃未保存的修改？',exit:'退出修改器？'}[modal.type] }}</h2><button class="icon-button" :disabled="busy" @click="modal = null" aria-label="关闭"><X :size="19" /></button></div><template v-if="modal.type === 'create' || modal.type === 'rename'"><p>名称仅用于识别备份，不影响游戏存档内容。</p><label for="backup-name">备份名称</label><input id="backup-name" v-model="backupName" maxlength="200" autofocus @keyup.enter="confirmModal" /></template><template v-else-if="modal.type === 'restore'"><p>将用“{{ modal.backup?.name }}”覆盖当前存档。当前完整存档会先保存为自动恢复点，多余的 .sav 文件也会保留在恢复点中。</p><p v-if="changedCount">当前未保存的参数修改将被放弃。</p></template><p v-else-if="modal.type === 'delete'">删除“{{ modal.backup?.name }}”及其备份文件。此操作不会删除当前游戏存档。</p><template v-else-if="modal.type === 'save'"><p>保存 {{ changedCount }} 项修改。保存前将创建完整备份。</p><div v-if="editPreview?.story" class="story-save-preview"><strong>剧情阶段 {{ editPreview.story.old.stage }} → {{ editPreview.story.value.stage }}</strong><p>{{ editPreview.story.old.name || '未识别任务' }} → {{ editPreview.story.value.name }}</p><p>原目标：{{ editPreview.story.old.objective || '未记录' }}</p><p>目标任务：{{ editPreview.story.value.objective }}</p><p class="story-risk">{{ editPreview.story.warning }}</p></div><div class="change-preview"><div v-for="change in editPreview?.weapon_skills" :key="'skills-' + change.index"><strong>{{ weaponPosition(change.index) }} · 附加技能</strong><span>{{ change.old.map(skillName).join('、') || '无' }} → {{ change.value.map(skillName).join('、') || '无' }}</span></div><div v-for="field in editPreview?.applied" :key="field.id"><strong>{{ field.id }}</strong><span class="mono">{{ field.old }} → {{ field.value }} {{ field.clamped ? '（自动修正）' : '' }}</span></div></div></template><p v-else-if="modal.type === 'exit'">退出后可以关闭网页。未保存的修改会被放弃。</p><p v-else>重新读取将放弃当前输入的修改。</p><div class="modal-actions"><button class="button secondary" :disabled="busy" @click="modal = null">取消</button><button :class="['button', modal.type === 'delete' ? 'delete-button' : 'primary']" :disabled="busy || ((modal.type === 'create' || modal.type === 'rename') && !backupName.trim()) || (['create','restore','save'].includes(modal.type) && (status.game_running || status.pending_recovery > 0))" @click="confirmModal"><LoaderCircle v-if="busy" :size="17" class="spinning" />{{ busy ? '处理中…' : '确认' }}</button></div></section></div>
  </div>
</template>
