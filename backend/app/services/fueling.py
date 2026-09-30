"""航油加注业务规则：状态流转、字段校验与结算清单出单自检。

结算清单不是简单导出：出单前先逐行自检——加油车辆、油料类型、实际油量（加注量）、
签收状态、操作人员必须齐全，计划/实际油量要能解析成数值，计划油量不得超过单次加注
上限；再核对列表页、详情页与对账汇总三处口径的条数一致。任一环节不通过就中止，指出
差在哪一行、原因是什么，且不留下半份清单。修正后可从失败行继续。并发提交时按版本号
只保留最新一版结果，历史清单的条数每次出单都按当前数据重算。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "fueling"
REQUIRED_FIELDS = ["加油编号", "对应航班", "油料类型"]
STATUS_ORDER = ["待加油", "加油中", "已加注", "已签收"]
ACTION_RULES = {"开始加油": "加油中", "完成加注": "已加注", "签收确认": "已签收"}
NEGATIVE_ACTIONS = []

# 结算模板表头：出单列顺序一律以此为准，避免与结算模板错位
SETTLEMENT_COLUMNS = [
    "加油编号",
    "对应航班",
    "油料类型",
    "加油车辆",
    "操作人员",
    "计划油量(升)",
    "实际油量(升)",
    "油量差值(升)",
    "签收状态",
]
# 出单时每行必须随单带出的字段
SETTLEMENT_REQUIRED_FIELDS = ["加油车辆", "油料类型", "实际油量", "操作人员"]
# 单次加注计划油量上限（升），超出即判为异常行
MAX_SINGLE_FUEL_LITERS = 50_000
# 历史版本最多留几版（条数均按出单时数据重算）
SETTLEMENT_HISTORY_LIMIT = 10
# 允许在补录修正时写入的字段
EDITABLE_FIELDS = ["对应航班", "油料类型", "计划油量", "实际油量", "加油车辆", "操作人员"]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _is_blank(value: Any) -> bool:
    return value is None or not str(value).strip()


def _parse_liters(value: Any) -> tuple[float | None, str | None]:
    """把油量解析成升；返回 (数值, 错误标记)，错误标记为 missing / invalid。"""
    if _is_blank(value):
        return None, "missing"
    if isinstance(value, bool):
        return None, "invalid"
    try:
        return float(str(value).strip().replace(",", "")), None
    except (TypeError, ValueError):
        return None, "invalid"


class FuelingService:
    def __init__(self) -> None:
        # 出单是“读全部行 + 写清单状态”的复合操作，用一把锁保证版本登记与发布互斥
        self._settle_lock = threading.Lock()
        self._generation = 0
        self._job: dict[str, Any] | None = None
        self._published: dict[str, Any] | None = None
        self._aborted: dict[str, Any] | None = None
        self._history: list[dict[str, Any]] = []

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("加油编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """补录修正：结算自检拦下的行，修正后从失败行续作。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"加油任务 {entry_id} 不存在或已归档"
        updates = {key: values[key] for key in EDITABLE_FIELDS if key in values}
        if not updates:
            return None, "没有可更新的字段（允许修正：对应航班、油料类型、计划油量、实际油量、加油车辆、操作人员）"
        entry.update(updates)
        return entry, ""

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"加油任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于航油加注可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"加油任务已{action}"

    # ------------------------------------------------------------------
    # 结算清单
    # ------------------------------------------------------------------

    def latest_settlement(self) -> dict[str, Any]:
        """读取最近一次出单状态：已发布的最新一版、最近一次中止现场、版本计数。"""
        with self._settle_lock:
            return {
                "module": MODULE,
                "counter": self._generation,
                "job": dict(self._job) if self._job else None,
                "published": self._published,
                "aborted": self._aborted,
            }

    def generate_settlement(self, *, resume: bool = False) -> dict[str, Any]:
        """生成结算清单。

        resume=True 且上一版确实中止在某一行时，从失败行继续自检；否则从第一行
        重新开始。并发调用各自拿到单调递增的版本号，发布时发现自己不是最新版本
        即作废，只保留最新一版结果。
        """
        rows = store.rows(MODULE)
        with self._settle_lock:
            start_index = 0
            resumed_from: int | None = None
            source_total = len(rows)
            if resume and self._aborted is not None:
                checkpoint = self._aborted.get("resume") or {}
                snapshot_total = self._aborted.get("snapshot_total")
                start_index = max(int(checkpoint.get("from_row", 1)) - 1, 0)
                if snapshot_total == source_total and 0 <= start_index < source_total:
                    resumed_from = self._aborted.get("generation")
                else:
                    # 源数据条数变了，按旧行号续作会错位，从头重检
                    start_index = 0
            self._generation += 1
            generation = self._generation
            self._aborted = None
            self._job = {
                "generation": generation,
                "status": "running",
                "started_at": _now(),
                "start_row": start_index + 1,
                "resumed_from": resumed_from,
            }

        ordered = sorted(rows, key=lambda row: int(row.get("id", 0)))

        # 第一步：逐行自检，发现差异立即中止并指出差在哪一行
        for index in range(start_index, len(ordered)):
            reasons = self._inspect_row(ordered[index])
            if reasons:
                return self._abort(generation, ordered, index, reasons, source_total)

        # 第二步：列表页 / 详情页 / 对账汇总三处条数核对
        issue_index, reasons, counts = self._reconcile(ordered)
        if reasons:
            return self._abort(generation, ordered, issue_index, reasons, source_total, counts)

        # 全部通过才构造清单行——半份清单不会被存下来
        items = [self._build_item(row) for row in ordered]
        summary = self._summarize(items)
        result = {
            "module": MODULE,
            "status": "complete",
            "generation": generation,
            "columns": list(SETTLEMENT_COLUMNS),
            "items": items,
            "issues": [],
            "summary": summary,
            "counts": counts,
            "resume": None,
            "snapshot_total": source_total,
            "generated_at": _now(),
            "message": (
                f"结算清单第 {generation} 版已生成：共 {summary['total']} 行，"
                f"已签收 {summary['signed']} 行；列表页、详情页与对账汇总条数一致"
                f"（各 {counts['list_total']} 条）。"
            ),
        }
        record = {
            "generation": generation,
            "generated_at": result["generated_at"],
            "total": summary["total"],
            "signed": summary["signed"],
            "unsigned": summary["unsigned"],
            "planned_total": summary["planned_total"],
            "actual_total": summary["actual_total"],
            "recalculated": True,
        }
        with self._settle_lock:
            if self._generation != generation:
                return self._superseded(generation)
            self._history = [record] + self._history[: SETTLEMENT_HISTORY_LIMIT - 1]
            result["history"] = list(self._history)
            self._published = result
            self._aborted = None
            self._job = {
                "generation": generation,
                "status": "complete",
                "started_at": self._job["started_at"],
                "finished_at": _now(),
            }
        return result

    def _inspect_row(self, row: dict[str, Any]) -> list[str]:
        """单行自检，返回异常原因列表；空列表表示该行通过。"""
        reasons: list[str] = []

        for field in SETTLEMENT_REQUIRED_FIELDS:
            if _is_blank(row.get(field)):
                label = "加注量（实际油量）" if field == "实际油量" else field
                reasons.append(f"{label}缺失")

        planned, planned_error = _parse_liters(row.get("计划油量"))
        if planned_error == "missing":
            reasons.append("计划油量缺失")
        elif planned_error == "invalid":
            reasons.append(f"计划油量无法解析为数值：{row.get('计划油量')!r}")
        elif planned is not None and planned > MAX_SINGLE_FUEL_LITERS:
            reasons.append(
                f"计划油量 {planned:g} 升超出单次加注上限 {MAX_SINGLE_FUEL_LITERS:g} 升"
            )

        if not _is_blank(row.get("实际油量")):
            actual, actual_error = _parse_liters(row.get("实际油量"))
            if actual_error == "invalid":
                reasons.append(f"加注量（实际油量）无法解析为数值：{row.get('实际油量')!r}")

        status = row.get("status")
        if status not in STATUS_ORDER:
            reasons.append(f"签收状态无法识别：{status!r}")

        return reasons

    def _reconcile(
        self, ordered: list[dict[str, Any]]
    ) -> tuple[int, list[str], dict[str, int]]:
        """核对列表页、详情页与对账汇总的条数；返回 (差异行下标, 原因, 三处计数)。"""
        list_rows, list_total = self.list_entries(page=1, size=10_000)
        list_ids = {int(row["id"]) for row in list_rows if row.get("id") is not None}

        ordered_ids = [int(row["id"]) for row in ordered if row.get("id") is not None]
        detail_count = sum(
            1 for entry_id in ordered_ids if store.find(MODULE, entry_id) is not None
        )
        summary_total = len(ordered)
        counts = {
            "list_total": list_total,
            "detail_count": detail_count,
            "summary_total": summary_total,
            "matched": int(list_total == detail_count == summary_total)
            and set(ordered_ids) == list_ids,
        }

        if set(ordered_ids) != list_ids or list_total != summary_total:
            missing = [entry_id for entry_id in ordered_ids if entry_id not in list_ids]
            index = ordered_ids.index(missing[0]) if missing else len(ordered)
            return index, [
                f"条数核对不一致：列表页读到 {list_total} 条，"
                f"对账汇总是 {summary_total} 条（含已签收记录，不得整条丢失）"
            ], counts

        if detail_count != list_total:
            return len(ordered), [
                f"条数核对不一致：详情页读到 {detail_count} 条，列表页读到 {list_total} 条"
            ], counts

        return len(ordered), [], counts

    def _build_item(self, row: dict[str, Any]) -> dict[str, Any]:
        planned, _ = _parse_liters(row.get("计划油量"))
        actual, _ = _parse_liters(row.get("实际油量"))
        difference = None if planned is None or actual is None else round(actual - planned, 3)
        return {
            "id": row.get("id"),
            "加油编号": row.get("加油编号"),
            "对应航班": row.get("对应航班"),
            "油料类型": row.get("油料类型"),
            "加油车辆": row.get("加油车辆"),
            "操作人员": row.get("操作人员"),
            "计划油量(升)": planned,
            "实际油量(升)": actual,
            "油量差值(升)": difference,
            # 签收状态以流程状态字段为准，保证已签收行不会在清单里变成空白
            "签收状态": row.get("status"),
        }

    def _summarize(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        signed = sum(1 for item in items if item["签收状态"] == STATUS_ORDER[-1])
        planned_values = [item["计划油量(升)"] for item in items if item["计划油量(升)"] is not None]
        actual_values = [item["实际油量(升)"] for item in items if item["实际油量(升)"] is not None]
        return {
            "total": len(items),
            "signed": signed,
            "unsigned": len(items) - signed,
            "planned_total": round(sum(planned_values), 3),
            "actual_total": round(sum(actual_values), 3),
        }

    def _make_issue(
        self, ordered: list[dict[str, Any]], index: int, reasons: list[str]
    ) -> dict[str, Any]:
        if 0 <= index < len(ordered):
            row = ordered[index]
            return {
                "row": index + 1,
                "id": row.get("id"),
                "加油编号": row.get("加油编号") or "",
                "reasons": reasons,
            }
        return {"row": index + 1, "id": None, "加油编号": "", "reasons": reasons}

    def _abort(
        self,
        generation: int,
        ordered: list[dict[str, Any]],
        index: int,
        reasons: list[str],
        source_total: int,
        counts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """登记一次中止现场：不落任何清单行，只记录失败行，供修正后续作。"""
        issue = self._make_issue(ordered, index, reasons)
        label = f"第 {issue['row']} 行"
        if issue["加油编号"]:
            label += f"（{issue['加油编号']}）"
        result = {
            "module": MODULE,
            "status": "aborted",
            "generation": generation,
            "columns": list(SETTLEMENT_COLUMNS),
            # 中止时 items 必为空：不留下半份清单
            "items": [],
            "issues": [issue],
            "summary": None,
            "counts": counts,
            "resume": {"from_row": issue["row"], "label": label},
            "snapshot_total": source_total,
            "history": list(self._history),
            "generated_at": _now(),
            "message": (
                f"结算清单自检未通过，已在{label}中止：{'；'.join(reasons)}。"
                f"本次未生成清单，请修正该行后从失败行继续。"
            ),
        }
        with self._settle_lock:
            if self._generation != generation:
                return self._superseded(generation)
            self._aborted = result
            self._job = {
                "generation": generation,
                "status": "aborted",
                "failed_row": issue["row"],
                "finished_at": _now(),
            }
        return result

    def _superseded(self, generation: int) -> dict[str, Any]:
        return {
            "module": MODULE,
            "status": "superseded",
            "generation": generation,
            "latest_generation": self._generation,
            "columns": list(SETTLEMENT_COLUMNS),
            "items": [],
            "issues": [],
            "summary": None,
            "counts": None,
            "resume": None,
            "history": list(self._history),
            "generated_at": _now(),
            "message": (
                f"第 {generation} 版生成请求已被更新的第 {self._generation} 版请求取代，"
                "本次结果作废，仅保留最新一版结算清单。"
            ),
        }
