/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 解析指标复核详情：动作清单里每一条真正计入的解析路由决策。 */
export type ReviewDecisionItem = {
  决策编号: string
  关联批次: string
  命中解析器: string
  回退次数: string
  决策时间: string
  status: string
  计入分子: boolean
}

/** 服务端返回的读数复核详情：渠道与时间窗筛选已在服务端完成。 */
export type MetricReviewDetail = {
  reading: EntryRow
  统计窗口: string
  来源渠道: string
  当前: { 回退率: string; 回退决策计数: string; 路由决策计数: string }
  原账: {
    回退率: string
    回退决策计数: string
    路由决策计数: string
    计入决策编号: string
  } | null
  动作清单: ReviewDecisionItem[]
  迟到凭据: ReviewDecisionItem[]
  待补算凭据: ReviewDecisionItem[]
  窗口批次: { 批次记录编号: string; 关联投递批次: string; 总行数: string; 完成时间: string }[]
  核对冲突: boolean
  复核结论: string
  message: string
}
