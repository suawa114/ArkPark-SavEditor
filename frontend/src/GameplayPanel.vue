<script setup lang="ts">
import { computed, ref } from 'vue'
import { BookOpen, ChartNoAxesCombined, Crosshair, Package, Plus, Search, X } from 'lucide-vue-next'
import { weaponPosition } from './labels'
import type { GameplayData, NumericField } from './api'

const props = defineProps<{
  data: GameplayData; fields: NumericField[]; changes: Record<string, string>;
  skills: Record<string, string[]>; readonly: boolean;
}>()
const emit = defineEmits<{
  update: [field: NumericField, value: string]; skills: [index: number, values: string[]];
}>()
const active = ref<'weapons' | 'inventory' | 'story' | 'statistics'>('weapons')
const categories = [
  {id:'weapons' as const, label:'枪械与技能', icon:Crosshair},
  {id:'inventory' as const, label:'背包物资', icon:Package},
  {id:'story' as const, label:'剧情进度', icon:BookOpen},
  {id:'statistics' as const, label:'游戏统计', icon:ChartNoAxesCombined},
]
function selectCategory(id: typeof active.value) { active.value = id; search.value = '' }
function navigateTabs(event: KeyboardEvent) {
  const index = categories.findIndex(item => item.id === active.value)
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? categories.length - 1 : event.key === 'ArrowRight' ? (index + 1) % categories.length : event.key === 'ArrowLeft' ? (index - 1 + categories.length) % categories.length : -1
  if (next < 0) return
  event.preventDefault()
  const item = categories[next]!
  selectCategory(item.id)
  const button = (event.currentTarget as HTMLElement).querySelector<HTMLButtonElement>(`#gameplay-tab-${item.id}`)
  button?.focus(); button?.scrollIntoView({block:'nearest', inline:'nearest'})
}
const search = ref('')
const storySearch = ref('')
const storyTasks = computed(() => props.data.story_options.filter(task =>
  `${task.stage} ${task.name ?? ''} ${task.objective ?? ''} ${task.details ?? ''}`.toLowerCase().includes(storySearch.value.toLowerCase())))
const targetStage = computed(() => props.data.story.field_id ? value(props.data.story.field_id) : props.data.story.stage)
const targetTask = computed(() => props.data.story_options.find(task => task.stage === targetStage.value))
const fieldsById = computed(() => new Map(props.fields.map(field => [field.id, field])))
const inventory = computed(() => props.data.inventory.filter(item => `${item.name} ${item.index} ${item.item_id}`.toLowerCase().includes(search.value.toLowerCase())))
const statistics = computed(() => props.data.statistics.filter(item => item.label.toLowerCase().includes(search.value.toLowerCase())))
function field(id: string) { return fieldsById.value.get(id) }
function value(id: string) { return props.changes[id] ?? field(id)?.value ?? '' }
function update(id: string, event: Event) {
  const target = field(id)
  if (target) emit('update', target, (event.target as HTMLInputElement).value)
}
function skillValues(index: number) {
  return props.skills[String(index)] ?? props.data.weapons.find(weapon => weapon.index === index)?.skills.map(skill => skill.id) ?? []
}
function setSkill(index: number, position: number, id: string) {
  const values = [...skillValues(index)]
  values[position] = id
  emit('skills', index, values)
}
function addSkill(index: number) {
  const first = props.data.skill_options[0]
  if (first) emit('skills', index, [...skillValues(index), first.id])
}
function removeSkill(index: number, position: number) {
  emit('skills', index, skillValues(index).filter((_, i) => i !== position))
}
function chooseStory(stage: string) {
  const id = props.data.story.field_id
  const target = id ? field(id) : undefined
  if (target && props.data.story.editable) emit('update', target, stage)
}
function options(id: string) {
  return props.data.skill_options.some(option => option.id === id)
    ? props.data.skill_options : [{id, name: `未识别技能 (${id})`}, ...props.data.skill_options]
}
</script>

<template>
  <section class="panel gameplay-panel">
    <div class="gameplay-tabs" role="tablist" aria-label="游戏数据分类" @keydown="navigateTabs">
      <button v-for="item in categories" :key="item.id" :id="`gameplay-tab-${item.id}`" :class="{active:active === item.id}" role="tab" :aria-selected="active === item.id" :tabindex="active === item.id ? 0 : -1" :aria-controls="`gameplay-content-${item.id}`" @click="selectCategory(item.id)"><component :is="item.icon" :size="16" />{{ item.label }}</button>
    </div>
    <div :id="`gameplay-content-${active}`" role="tabpanel" :aria-labelledby="`gameplay-tab-${active}`" tabindex="0" class="gameplay-content">
    <div v-if="data.reason" class="gameplay-note">{{ data.reason }}</div>

    <template v-if="active === 'weapons'">
      <div class="weapon-grid">
        <article v-for="weapon in data.weapons" :key="weapon.index" class="weapon-card">
          <div class="weapon-heading"><h3>{{ weaponPosition(weapon.index) }}</h3><span class="chip muted">{{ weapon.name }}</span></div>
          <div class="weapon-fields">
            <label v-for="item in weapon.fields" :key="item.field_id"><span>{{ item.label }}</span><input type="text" inputmode="decimal" :aria-label="`${weaponPosition(weapon.index)} ${item.label}`" :value="value(item.field_id)" :disabled="readonly || !field(item.field_id)?.editable" @input="update(item.field_id, $event)" /><small v-if="field(item.field_id)">{{ field(item.field_id)?.minimum }} → {{ field(item.field_id)?.maximum }}</small></label>
          </div>
          <div class="weapon-subheading"><h4>附加技能 <span class="count">{{ skillValues(weapon.index).length }}</span></h4><button class="button text" :disabled="readonly || !weapon.skills_editable || !data.skill_options.length" @click="addSkill(weapon.index)"><Plus :size="15" />添加技能</button></div>
          <div class="skill-list"><div v-for="(id, index) in skillValues(weapon.index)" :key="index" class="skill-row"><span>{{ index + 1 }}</span><select :value="id" :aria-label="`${weaponPosition(weapon.index)} 第 ${index + 1} 项技能`" :disabled="readonly || !weapon.skills_editable" @change="setSkill(weapon.index, index, ($event.target as HTMLSelectElement).value)"><option v-for="option in options(id)" :key="option.id" :value="option.id">{{ option.name }}</option></select><button class="icon-button" :disabled="readonly || !weapon.skills_editable" :aria-label="`移除${weaponPosition(weapon.index)} 第 ${index + 1} 项技能`" @click="removeSkill(weapon.index, index)"><X :size="16" /></button></div><p v-if="!skillValues(weapon.index).length" class="gameplay-note">没有附加技能。</p></div>
          <p v-if="weapon.skills_reason" class="gameplay-note">{{ weapon.skills_reason }}</p>
        </article>
      </div>
      <p v-if="!data.weapons.length" class="empty">当前存档没有可识别的发射器。</p>
      <p class="gameplay-note gameplay-bottom-note">修改器不限制技能项数，保留顺序和重复项。游戏通常生成 3 项，超过 3 项的加载和效果尚未实测；重复相同技能不代表效果叠加。所有改动统一使用下方“保存修改”。</p>
    </template>

    <template v-else-if="active === 'inventory'">
      <div class="gameplay-toolbar"><label class="searchbox"><span class="search-label">背包物资</span><Search :size="15" /><input v-model="search" placeholder="名称、编号或槽位" aria-label="搜索背包物资" /></label><span class="results-count">{{ inventory.length }} 个槽位</span></div>
      <div class="table-scroll"><table><thead><tr><th>存档槽位</th><th>物品</th><th>原数量</th><th>修改数量</th></tr></thead><tbody><tr v-for="item in inventory" :key="item.index" :class="{changed: item.field_id && item.field_id in changes}"><td class="mono">{{ item.index }}</td><td><strong>{{ item.name }}</strong><p class="inventory-description">{{ item.description }}</p><small class="inventory-id">物品编号 {{ item.item_id }}</small></td><td class="mono original">{{ item.quantity }}</td><td><input v-if="item.field_id" type="text" inputmode="numeric" class="value-input mono" :aria-label="`槽位 ${item.index} ${item.name} 数量`" :value="value(item.field_id)" :disabled="readonly || !field(item.field_id)?.editable" @input="update(item.field_id, $event)" /><span v-else class="gameplay-note">只读</span></td></tr><tr v-if="!inventory.length"><td colspan="4" class="empty">没有符合条件的物品。</td></tr></tbody></table></div>
      <p class="gameplay-note gameplay-bottom-note">槽位编号采用存档中的原始编号，从 0 开始；只修改现有物品数量。</p>
    </template>

    <template v-else-if="active === 'story'">
      <div v-if="data.story.available" class="story-content"><div class="story-heading"><span class="chip muted">进度 {{ data.story.stage }}</span><h3>{{ data.story.name || '当前任务' }}</h3></div><h4>当前完成目标</h4><p class="story-objective">{{ data.story.objective || '当前阶段未提供目标文本。' }}</p><h4>任务详情</h4><p>{{ data.story.details || '当前任务没有额外说明。' }}</p><div v-if="data.story.goals?.length" class="stat-grid"><div v-for="goal in data.story.goals" :key="goal.label" class="stat-item"><span>{{ goal.label }}</span><strong>{{ goal.value }}</strong></div></div></div>
      <div v-else class="empty"><span v-if="data.story.stage !== undefined">剧情索引 {{ data.story.stage }} · </span>{{ data.story.reason || '当前存档没有可识别的剧情进度。' }}</div>
      <div v-if="data.story_options.length" class="story-editor">
        <div class="story-editor-heading"><h3>选择目标剧情任务</h3><span class="chip muted">{{ data.story_options.length }} 个阶段记录</span></div>
        <p class="story-risk">剧情调整为实验功能：仅修改剧情索引，不补发奖励或同步角色、地图与前置任务状态，跳转后可能无法继续任务。保存前会自动备份完整存档。</p>
        <div class="story-target" aria-live="polite"><span>待保存目标 · 阶段 {{ targetStage }}</span><strong>{{ targetTask?.name || '未识别任务' }}</strong><p>{{ targetTask?.objective || '该阶段没有已核实的目标。' }}</p><small v-if="targetStage !== data.story.stage">当前阶段 {{ data.story.stage }} → {{ targetStage }}；点击下方“保存修改”才会写入。</small></div>
        <label class="searchbox"><span class="search-label">剧情任务</span><Search :size="15" /><input v-model="storySearch" placeholder="标题、目标、详情或阶段" aria-label="搜索剧情任务" /></label>
        <div class="story-task-list" role="radiogroup" aria-label="目标剧情任务">
          <label v-for="task in storyTasks" :key="task.stage" :class="['story-task', {selected:targetStage === task.stage, unavailable:!task.selectable}]">
            <input type="radio" name="story-stage" :value="task.stage" :checked="targetStage === task.stage" :disabled="readonly || !data.story.editable || !task.selectable" :aria-label="`阶段 ${task.stage} ${task.name || '无任务文本'}`" @change="chooseStory(task.stage)" />
            <span><strong><small>阶段 {{ task.stage }}</small>{{ task.name || '无任务标题（不可选）' }}<em v-if="task.stage === data.story.stage">当前</em></strong><span class="task-objective">{{ task.objective || '未提供目标，不作为跳转选项。' }}</span><span v-if="task.details" class="task-details">{{ task.details }}</span></span>
          </label>
          <p v-if="!storyTasks.length" class="empty">没有符合条件的任务。</p>
        </div>
      </div>
      <p class="gameplay-note gameplay-bottom-note">任务名称、详情和目标来自对应阶段的游戏文本；部分目标中的计数是静态文字，不代表实时完成数量。游戏内尚未保存的进度需退出游戏后重新读取。</p>
    </template>

    <template v-else>
      <div class="gameplay-toolbar"><label class="searchbox"><span class="search-label">游戏统计</span><Search :size="15" /><input v-model="search" placeholder="统计名称" aria-label="搜索游戏统计" /></label><span class="results-count">{{ statistics.length }} 项统计</span></div>
      <div class="stat-grid"><div v-for="item in statistics" :key="item.field_id ?? item.label" class="stat-item"><span>{{ item.label }}</span><strong class="mono">{{ item.value }}</strong><small v-if="item.note" class="stat-note">{{ item.note }}</small></div></div><p v-if="!statistics.length" class="empty">当前存档没有符合条件的统计值。</p>
      <p class="gameplay-note gameplay-bottom-note">统计读取已保存的存档，只读展示；缺少或无法完整解析的新增指标显示“未记录”。游戏内变化保存后，请重新读取存档。</p>
    </template>
    </div>
  </section>
</template>
