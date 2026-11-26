import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, MetricReviewDetail, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

/** 解析指标复核详情：动作清单、迟到凭据与原账都由服务端按渠道和窗口筛好。 */
export async function fetchMetricReviewDetail(id: number): Promise<MetricReviewDetail> {
  const response = await request(`/api/parse_metric/${id}/review_detail`)
  if (!response.ok) throw new Error('复核详情读取失败')
  return await response.json()
}

/** 更正入口：把选中的迟到凭据并入读数，服务端做归属校验。 */
export async function correctMetricReading(
  id: number,
  decisionNos: string[],
): Promise<ActionResult> {
  const response = await request(`/api/parse_metric/${id}/correction`, {
    method: 'POST',
    body: JSON.stringify({ decision_nos: decisionNos, reviewer: 'web-reviewer' }),
  })
  return await response.json()
}

/** 沿用旧证据核对当次发布：迟到凭据转待补算，读数保持原账。 */
export async function deferMetricReceipts(id: number): Promise<ActionResult> {
  const response = await request(`/api/parse_metric/${id}/defer_receipts`, {
    method: 'POST',
    body: JSON.stringify({ reviewer: 'web-reviewer' }),
  })
  return await response.json()
}
