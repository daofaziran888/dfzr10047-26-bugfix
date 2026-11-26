"""解析指标领域服务：按窗口和来源汇总吞吐、隔离率与回退率统计，产出指标读数数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ParseMetricService(BaseModuleService):
    MODULE = "parse_metric"
    ENTITY = "指标读数"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["指标读数编号", "统计窗口", "来源渠道", "解析吞吐行数", "平均解析耗时", "隔离率", "回退率", "采集时间"]
    REQUIRED_FIELDS = ["指标读数编号", "统计窗口", "来源渠道"]
    STATUS_ORDER = ["统计中", "已结算", "待复核", "已作废"]
    ACTION_RULES = {"提交结算": "已结算", "复核读数": "待复核", "作废读数": "已作废"}
    NEGATIVE_ACTIONS = ["作废读数"]

    @staticmethod
    def _int(row: dict[str, Any], field: str) -> int:
        text = str(row.get(field) or "").strip()
        return int(text) if text.isdigit() else 0

    @staticmethod
    def _float(row: dict[str, Any], field: str) -> float:
        try:
            return float(str(row.get(field) or "").strip())
        except ValueError:
            return 0.0

    # ------------------------------------------------------------------
    # 窗口与归属工具：统计窗口按日对齐，路由决策按来源渠道归属
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_window(window: Any) -> str:
        """统计窗口归一为 8 位日期（如 20260901）；无法识别时返回空串。"""
        digits = "".join(ch for ch in str(window or "") if ch.isdigit())
        return digits[:8] if len(digits) >= 8 else ""

    @classmethod
    def _window_of(cls, time_text: Any) -> str:
        """从时间文本提取所属统计窗口：2026-09-01T08:20:00 → 20260901。"""
        return cls._normalize_window(str(time_text or "")[:10])

    @staticmethod
    def _split_nos(text: Any) -> list[str]:
        return [part.strip() for part in str(text or "").split(",") if part.strip()]

    def _channel_batches(self, channel: str) -> set[str]:
        return {
            str(r.get("批次编号")) for r in store.rows("file_intake")
            if r.get("来源渠道") == channel
        }

    def _route_decisions(self, channel: str, window: str = "") -> list[dict[str, Any]]:
        """归属校验后的路由决策：本渠道投递批次、落在统计窗口内，同一决策编号只算一次。"""
        intake_batches = self._channel_batches(channel)
        seen: set[str] = set()
        decisions: list[dict[str, Any]] = []
        for row in store.rows("parser_route"):
            if str(row.get("关联批次")) not in intake_batches:
                continue  # 别的来源不得进入本次读数
            if window and self._window_of(row.get("决策时间")) != window:
                continue  # 窗外决策不得进入本次读数
            decision_no = str(row.get("决策编号") or "")
            if decision_no and decision_no in seen:
                continue  # 同一条路由决策只算一次
            seen.add(decision_no)
            decisions.append(row)
        return decisions

    def real_quarantine_rate(self, channel: str,
                             window: str | None = None) -> tuple[float, int, int]:
        """隔离率来自坏行隔离的真实计数，不做估算。

        返回 (隔离率, 隔离行数, 总行数)。总行数取该来源已闭合批次的行数之和；
        给定统计窗口时只计入窗口内闭合的批次与窗口内隔离的行。
        """
        norm_window = self._normalize_window(window) if window else ""
        intake_batches = self._channel_batches(channel)
        total_rows = 0
        for batch in store.rows("parse_batch"):
            if str(batch.get("关联投递批次")) not in intake_batches:
                continue
            if norm_window and self._window_of(batch.get("完成时间")) != norm_window:
                continue
            total_rows += self._int(batch, "总行数")
        quarantined = sum(
            1 for r in store.rows("row_quarantine")
            if str(r.get("关联批次")) in intake_batches
            and (not norm_window or self._window_of(r.get("隔离时间")) == norm_window)
        )
        rate = quarantined / total_rows if total_rows > 0 else 0.0
        return round(rate, 4), quarantined, total_rows

    def real_fallback_rate(self, channel: str,
                           window: str | None = None) -> tuple[float, int, int]:
        """回退率来自解析路由的真实回退计数。

        返回 (回退率, 发生回退的决策数, 决策总数)。给定统计窗口时只计入窗口内、
        本渠道的路由决策；别的来源和窗口不得进入本次读数。
        """
        norm_window = self._normalize_window(window) if window else ""
        decisions = self._route_decisions(channel, norm_window)
        fallbacks = sum(1 for r in decisions if self._int(r, "回退次数") > 0)
        rate = fallbacks / len(decisions) if decisions else 0.0
        return round(rate, 4), fallbacks, len(decisions)

    # ------------------------------------------------------------------
    # 结算：读数只计入本渠道本窗口的真实明细，并留存计入证据
    # ------------------------------------------------------------------
    @staticmethod
    def _snapshot_original(row: dict[str, Any]) -> None:
        """首次改账前保留原账读数与证据，历史日报可随时核对旧值。"""
        if row.get("原账回退率"):
            return
        row["原账回退率"] = str(row.get("回退率") or "")
        row["原账回退决策计数"] = str(row.get("回退决策计数") or "")
        row["原账路由决策计数"] = str(row.get("路由决策计数") or "")
        row["原账计入决策编号"] = str(row.get("计入决策编号") or "")

    def settle_reading(self, reading_no: str, window: str, channel: str,
                       collected_at: str) -> tuple[bool, str, dict[str, Any] | None]:
        """结算指标读数：吞吐、平均耗时、隔离率、回退率全部取自真实明细。

        只计入本来源渠道且落在统计窗口内的批次与路由决策；已结算的读数固定
        显示原账，迟到凭据须走更正入口，不得静默改账。
        """
        rows = store.rows(self.MODULE)
        target = next((r for r in rows if r.get("指标读数编号") == reading_no), None)
        if target is not None and target.get("status") == "已结算":
            return False, (
                f"指标读数 {reading_no} 已结算，原账固定显示；"
                f"迟到凭据请走更正入口，不得静默改账"
            ), None
        if target is not None and target.get("status") == "已作废":
            return False, f"指标读数 {reading_no} 已作废，不得重新结算", None

        norm_window = self._normalize_window(window)
        intake_batches = self._channel_batches(channel)
        batches = [
            b for b in store.rows("parse_batch")
            if str(b.get("关联投递批次")) in intake_batches
            and (not norm_window or self._window_of(b.get("完成时间")) == norm_window)
        ]
        throughput = sum(self._int(b, "总行数") for b in batches)
        window_batch_ids = {str(b.get("关联投递批次")) for b in batches}

        durations = [
            self._float(p, "解析耗时") for p in store.rows("field_parse")
            if str(p.get("关联批次")) in window_batch_ids
            and str(p.get("解析耗时") or "").replace(".", "").isdigit()
        ]
        avg_ms = sum(durations) / len(durations) if durations else 0.0

        quarantine_rate, quarantined, total_rows = self.real_quarantine_rate(
            channel, norm_window or None)
        fallback_rate, fallbacks, decisions = self.real_fallback_rate(
            channel, norm_window or None)
        evidence = self._route_decisions(channel, norm_window)
        counted_nos = [str(d.get("决策编号") or "") for d in evidence]
        fallback_nos = [
            no for no, d in zip(counted_nos, evidence)
            if self._int(d, "回退次数") > 0
        ]

        if target is None:
            entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            target = {"id": entry_id, "指标读数编号": reading_no}
            rows.append(target)
        elif target.get("计入决策编号"):
            # 重新结算已有证据的读数：原账与证据保留，供迟到修订核对旧值和新值
            self._snapshot_original(target)
        target.update({
            "统计窗口": window,
            "来源渠道": channel,
            "解析吞吐行数": str(throughput),
            "平均解析耗时": f"{avg_ms:.2f}",
            "隔离率": f"{quarantine_rate:.4f}",
            "回退率": f"{fallback_rate:.4f}",
            "采集时间": collected_at,
            "隔离行计数": str(quarantined),
            "批次总行计数": str(total_rows),
            "回退决策计数": str(fallbacks),
            "路由决策计数": str(decisions),
            "计入决策编号": ",".join(counted_nos),
            "回退决策编号": ",".join(fallback_nos),
            "status": "已结算",
            "pending": False,
            "abnormal": False,
        })
        return True, (
            f"指标读数 {reading_no} 已结算：吞吐 {throughput} 行，"
            f"隔离率 {quarantine_rate:.2%}（{quarantined}/{total_rows}），"
            f"回退率 {fallback_rate:.2%}（{fallbacks}/{decisions}）"
        ), dict(target)

    def needs_review(self, reading_no: str, reason: str) -> tuple[bool, str]:
        """标记待复核：结算读数与其他模块真实计数对不上时使用。"""
        for row in store.rows(self.MODULE):
            if row.get("指标读数编号") == reading_no:
                row["status"] = "待复核"
                row["pending"] = True
                row["abnormal"] = True
                row["复核原因"] = reason
                return True, f"指标读数 {reading_no} 已转入待复核：{reason}"
        return False, f"指标读数 {reading_no} 不存在"

    # ------------------------------------------------------------------
    # 复核详情：服务端返回真正属于该读数的动作清单、迟到凭据与原账
    # ------------------------------------------------------------------
    def _decision_item(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "决策编号": str(decision.get("决策编号") or ""),
            "关联批次": str(decision.get("关联批次") or ""),
            "命中解析器": str(decision.get("命中解析器") or ""),
            "回退次数": str(decision.get("回退次数") or "0"),
            "决策时间": str(decision.get("决策时间") or ""),
            "status": str(decision.get("status") or ""),
            "计入分子": self._int(decision, "回退次数") > 0,
        }

    def _late_receipt_nos(self, row: dict[str, Any]) -> list[str]:
        """属于本读数渠道与窗口、但既未计入也未转待补算的路由决策（迟到凭据）。"""
        channel = str(row.get("来源渠道") or "")
        window = self._normalize_window(row.get("统计窗口"))
        counted = set(self._split_nos(row.get("计入决策编号")))
        deferred = set(self._split_nos(row.get("待补算决策编号")))
        return [
            str(d.get("决策编号") or "")
            for d in self._route_decisions(channel, window)
            if str(d.get("决策编号") or "") not in counted | deferred
        ]

    def review_detail_by_id(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None
        return self.review_detail(str(row.get("指标读数编号") or ""))

    def review_detail(self, reading_no: str) -> dict[str, Any] | None:
        """复核详情：渠道与时间窗筛选在服务端完成，界面只展示不做二次过滤。"""
        row = next((r for r in store.rows(self.MODULE)
                    if r.get("指标读数编号") == reading_no), None)
        if row is None:
            return None
        channel = str(row.get("来源渠道") or "")
        window = self._normalize_window(row.get("统计窗口"))
        settled = row.get("status") in ("已结算", "待复核")

        counted = self._split_nos(row.get("计入决策编号"))
        deferred = self._split_nos(row.get("待补算决策编号"))
        counted_set, deferred_set = set(counted), set(deferred)

        counted_items: list[dict[str, Any]] = []
        late_items: list[dict[str, Any]] = []
        deferred_items: list[dict[str, Any]] = []
        if settled:
            for decision in self._route_decisions(channel, window):
                item = self._decision_item(decision)
                decision_no = item["决策编号"]
                if decision_no in counted_set:
                    counted_items.append(item)
                elif decision_no in deferred_set:
                    deferred_items.append(item)
                else:
                    late_items.append(item)

        intake_batches = self._channel_batches(channel)
        window_batches = [] if not settled else [
            {
                "批次记录编号": str(b.get("批次记录编号") or ""),
                "关联投递批次": str(b.get("关联投递批次") or ""),
                "总行数": str(b.get("总行数") or ""),
                "完成时间": str(b.get("完成时间") or ""),
            }
            for b in store.rows("parse_batch")
            if str(b.get("关联投递批次")) in intake_batches
            and (not window or self._window_of(b.get("完成时间")) == window)
        ]

        original = None
        if row.get("原账回退率"):
            original = {
                "回退率": str(row.get("原账回退率") or ""),
                "回退决策计数": str(row.get("原账回退决策计数") or "0"),
                "路由决策计数": str(row.get("原账路由决策计数") or "0"),
                "计入决策编号": str(row.get("原账计入决策编号") or ""),
            }

        conflict = bool(late_items)
        if conflict:
            message = (
                f"读数 {reading_no} 有 {len(late_items)} 笔迟到凭据与当次发布对不上，"
                f"请从更正入口并入，或沿用原证据并转待补算"
            )
        elif settled:
            message = f"读数 {reading_no} 动作清单与凭据核对一致"
        else:
            message = f"读数 {reading_no} 尚未结算，暂无计入证据"
        return {
            "reading": dict(row),
            "统计窗口": str(row.get("统计窗口") or ""),
            "来源渠道": channel,
            "当前": {
                "回退率": str(row.get("回退率") or ""),
                "回退决策计数": str(row.get("回退决策计数") or "0"),
                "路由决策计数": str(row.get("路由决策计数") or "0"),
            },
            "原账": original,
            "动作清单": counted_items,
            "迟到凭据": late_items,
            "待补算凭据": deferred_items,
            "窗口批次": window_batches,
            "核对冲突": conflict,
            "复核结论": str(row.get("复核结论") or ""),
            "message": message,
        }

    # ------------------------------------------------------------------
    # 更正入口与复核结论：归属校验在服务端完成，结论同步回复核清单
    # ------------------------------------------------------------------
    def apply_correction(self, reading_no: str, decision_nos: list[str],
                         reviewer: str,
                         corrected_at: str) -> tuple[bool, str, dict[str, Any] | None]:
        """更正入口：把迟到凭据并入读数，原账与证据保留。

        服务端归属校验：只接受真正属于本读数渠道与统计窗口的路由决策，
        别的来源和窗口不得进入本次读数；同一条路由决策只算一次。
        """
        row = next((r for r in store.rows(self.MODULE)
                    if r.get("指标读数编号") == reading_no), None)
        if row is None:
            return False, f"指标读数 {reading_no} 不存在", None
        if row.get("status") not in ("已结算", "待复核"):
            return False, f"指标读数 {reading_no} 尚未结算，请先提交结算再更正", None

        channel = str(row.get("来源渠道") or "")
        window = self._normalize_window(row.get("统计窗口"))
        by_no = {
            str(d.get("决策编号") or ""): d
            for d in self._route_decisions(channel, window)
        }
        submitted: list[str] = []
        for decision_no in decision_nos:
            decision_no = str(decision_no).strip()
            if decision_no and decision_no not in submitted:
                submitted.append(decision_no)
        foreign = [no for no in submitted if no not in by_no]
        if foreign:
            return False, (
                f"凭据 {'、'.join(foreign)} 不属于读数 {reading_no} 的"
                f"渠道 {channel} / 窗口 {window or row.get('统计窗口')}，"
                f"归属校验未通过，不得计入本次读数"
            ), None

        counted = self._split_nos(row.get("计入决策编号"))
        merged = list(counted)
        newly = [no for no in submitted if no not in merged]
        merged.extend(newly)
        if not newly:
            return False, (
                f"提交的凭据均已计入读数 {reading_no}，"
                f"同一条路由决策只算一次，无需更正"
            ), None

        old_rate = str(row.get("回退率") or "0")
        old_numerator = str(row.get("回退决策计数") or "0")
        old_denominator = str(row.get("路由决策计数") or "0")
        self._snapshot_original(row)

        numerator_nos = [no for no in merged if self._int(by_no[no], "回退次数") > 0]
        rate = round(len(numerator_nos) / len(merged), 4) if merged else 0.0
        deferred_remaining = [
            no for no in self._split_nos(row.get("待补算决策编号"))
            if no not in merged
        ]
        reviewer_name = reviewer or "复核员"
        row.update({
            "回退率": f"{rate:.4f}",
            "回退决策计数": str(len(numerator_nos)),
            "路由决策计数": str(len(merged)),
            "计入决策编号": ",".join(merged),
            "回退决策编号": ",".join(numerator_nos),
            "待补算决策编号": ",".join(deferred_remaining),
            "status": "已结算",
            "pending": False,
            "abnormal": False,
            "复核人": reviewer_name,
            "复核时间": corrected_at,
            "复核结论": (
                f"{reviewer_name} 更正：回退率 {old_rate}"
                f"（{old_numerator}/{old_denominator}）→ {rate:.4f}"
                f"（{len(numerator_nos)}/{len(merged)}），"
                f"并入迟到凭据 {len(newly)} 笔（{'、'.join(newly)}）"
            ),
        })
        return True, (
            f"指标读数 {reading_no} 已更正：回退率 {old_rate}"
            f"（{old_numerator}/{old_denominator}）→ {rate:.4f}"
            f"（{len(numerator_nos)}/{len(merged)}）；"
            f"原账及原证据已保留，可重新核对"
        ), dict(row)

    def defer_late_receipts(self, reading_no: str, reviewer: str,
                            decided_at: str) -> tuple[bool, str, dict[str, Any] | None]:
        """复核员决定沿用旧证据核对当次发布：迟到凭据转待补算，读数保持原账。"""
        row = next((r for r in store.rows(self.MODULE)
                    if r.get("指标读数编号") == reading_no), None)
        if row is None:
            return False, f"指标读数 {reading_no} 不存在", None
        if row.get("status") not in ("已结算", "待复核"):
            return False, f"指标读数 {reading_no} 尚未结算，没有可处理的迟到凭据", None

        late = self._late_receipt_nos(row)
        if not late:
            return False, f"指标读数 {reading_no} 没有待处理的迟到凭据", None
        deferred = self._split_nos(row.get("待补算决策编号"))
        deferred.extend(no for no in late if no not in deferred)
        reviewer_name = reviewer or "复核员"
        row.update({
            "待补算决策编号": ",".join(deferred),
            "status": "已结算",
            "pending": False,
            "复核人": reviewer_name,
            "复核时间": decided_at,
            "复核结论": (
                f"{reviewer_name} 决定沿用原证据核对当次发布；"
                f"{len(late)} 笔迟到凭据（{'、'.join(late)}）转待补算，"
                f"不进入本次读数"
            ),
        })
        return True, (
            f"指标读数 {reading_no} 维持原账 {row.get('回退率')}"
            f"（{row.get('回退决策计数')}/{row.get('路由决策计数')}）；"
            f"{len(late)} 笔迟到凭据已转待补算"
        ), dict(row)

    # ------------------------------------------------------------------
    # 核对：比率必须等于实际分子分母，计入证据必须真属于本渠道本窗口
    # ------------------------------------------------------------------
    def cross_check(self, reading_no: str) -> tuple[bool, str]:
        """与坏行隔离、解析路由的真实计数交叉核对，防止估算值入库。"""
        row = next(
            (r for r in store.rows(self.MODULE)
             if r.get("指标读数编号") == reading_no), None)
        if row is None:
            return False, f"指标读数 {reading_no} 不存在"

        channel = str(row.get("来源渠道") or "")
        window = self._normalize_window(row.get("统计窗口"))
        rate, quarantined, total_rows = self.real_quarantine_rate(
            channel, window or None)
        recorded_quarantined = self._int(row, "隔离行计数")
        if recorded_quarantined != quarantined:
            return False, (
                f"读数 {reading_no} 记录的隔离行 {recorded_quarantined} "
                f"与坏行隔离真实计数 {quarantined} 不一致，读数不得结算"
            )
        recorded_rate = self._float(row, "隔离率")
        if abs(recorded_rate - rate) > 0.0001:
            return False, (
                f"读数 {reading_no} 隔离率 {recorded_rate:.4f} "
                f"与真实计数 {rate:.4f}（{quarantined}/{total_rows}）不一致"
            )

        # 回退率：核对比率和实际分子分母，计入证据逐笔归属校验
        evidence = self._split_nos(row.get("计入决策编号"))
        if len(evidence) != len(set(evidence)):
            return False, (
                f"读数 {reading_no} 计入证据存在重复决策，同一条路由决策只算一次"
            )
        by_no = {
            str(d.get("决策编号") or ""): d
            for d in self._route_decisions(channel, window)
        }
        foreign = [no for no in evidence if no not in by_no]
        if foreign:
            return False, (
                f"读数 {reading_no} 计入证据 {'、'.join(foreign)} 不属于"
                f"渠道 {channel} / 窗口 {window or row.get('统计窗口')}，归属校验未通过"
            )
        actual_numerator = sum(
            1 for no in evidence if self._int(by_no[no], "回退次数") > 0)
        actual_denominator = len(evidence)
        recorded_numerator = self._int(row, "回退决策计数")
        recorded_denominator = self._int(row, "路由决策计数")
        if (recorded_numerator != actual_numerator
                or recorded_denominator != actual_denominator):
            return False, (
                f"读数 {reading_no} 记录的回退分子分母 "
                f"{recorded_numerator}/{recorded_denominator} "
                f"与计入证据实际 {actual_numerator}/{actual_denominator} 不一致"
            )
        expected_rate = (
            round(actual_numerator / actual_denominator, 4)
            if actual_denominator else 0.0
        )
        recorded_fallback_rate = self._float(row, "回退率")
        if abs(recorded_fallback_rate - expected_rate) > 0.0001:
            return False, (
                f"读数 {reading_no} 回退率 {recorded_fallback_rate:.4f} "
                f"与实际分子分母 {actual_numerator}/{actual_denominator}"
                f"（{expected_rate:.4f}）不符"
            )
        return True, (
            f"读数 {reading_no} 核对通过：隔离 {quarantined}/{total_rows} = {rate:.4f}，"
            f"回退 {actual_numerator}/{actual_denominator} = {expected_rate:.4f}"
        )

    # ------------------------------------------------------------------
    # 列表与动作：迟到凭据在复核清单上给出明确去向，结算走真实计数
    # ------------------------------------------------------------------
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        items, total = super().list_entries(
            filters=filters, keyword=keyword, status=status, page=page, size=size)
        annotated: list[dict[str, Any]] = []
        for item in items:
            row = dict(item)
            if row.get("status") in ("已结算", "待复核"):
                late = self._late_receipt_nos(row)
                row["迟到凭据数"] = str(len(late))
                row["核对冲突"] = bool(late)
            else:
                row["迟到凭据数"] = "0"
                row["核对冲突"] = False
            annotated.append(row)
        return annotated, total

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"指标读数 {entry_id} 不存在或已归档"
        if action == "提交结算":
            ok, msg, reading = self.settle_reading(
                str(entry.get("指标读数编号") or ""),
                str(entry.get("统计窗口") or ""),
                str(entry.get("来源渠道") or ""),
                str(entry.get("采集时间") or ""),
            )
            return (reading if ok else None), msg
        if action == "复核读数":
            ok, msg = self.needs_review(
                str(entry.get("指标读数编号") or ""),
                "结算读数与其他模块真实计数对不上",
            )
            return (dict(entry) if ok else None), msg
        return super().run_action(entry_id, action)

    def channel_throughput(self) -> dict[str, int]:
        """按来源渠道汇总吞吐行数，供分析看板消费。"""
        totals: dict[str, int] = {}
        for row in store.rows(self.MODULE):
            if row.get("status") == "已作废":
                continue
            channel = str(row.get("来源渠道") or "")
            totals[channel] = totals.get(channel, 0) + self._int(row, "解析吞吐行数")
        return totals
