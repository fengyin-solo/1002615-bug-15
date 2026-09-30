"""航油加注业务规则：任务流转、结算清单自检与断点续算都收在这里。

结算相关的几条硬性口径：
- 清单项统一按 SETTLEMENT_COLUMNS 的顺序与模板对齐；
- 「油量差值」一律由实际油量减计划油量现算，不再信任前端/历史缓存；
- 出清单前先自检：列表页、详情页与对账汇总的条数必须一致；
- 加注量缺失、计划油量超出单次加注上限只做告警，仍然进清单并写明原因；
- 生成过程逐行推进，中断后可从失败行续算，成功才整版发布，不留下半份清单；
- 每次生成都把历史数据条数重新计算；新请求会作废旧版本，只留最新一版结果。
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime
from typing import Any

from app.config import settings
from app.store import store

MODULE = "fueling"
REQUIRED_FIELDS = ["加油编号", "对应航班", "油料类型"]
STATUS_ORDER = ["待加油", "加油中", "已加注", "已签收"]
ACTION_RULES = {"开始加油": "加油中", "完成加注": "已加注", "签收确认": "已签收"}
NEGATIVE_ACTIONS = []

# 结算模板表头：列表页、清单导出与结算快照都按这个顺序，杜绝表头错位。
SETTLEMENT_COLUMNS = [
    "行号",
    "加油编号",
    "对应航班",
    "油料类型",
    "加油车辆",
    "操作人员",
    "签收状态",
    "计划油量",
    "实际油量",
    "油量差值",
]

# 单次加注上限（升）。计划油量超过该值要在清单里标原因。
MAX_SINGLE_FUEL_LITERS = 50_000

WARN_MISSING_ACTUAL = "加注量缺失"
WARN_OVER_LIMIT = f"计划油量超出单次加注上限（{MAX_SINGLE_FUEL_LITERS} 升）"


class SettlementAborted(Exception):
    """结算清单生成中止：携带差在第几行、差在哪的结构化说明。"""

    def __init__(self, message: str, *, line: int | None = None, discrepancy: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.line = line
        self.discrepancy = discrepancy or {"message": message}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_number(value: Any) -> float | None:
    """把计划/实际油量解析成数字；空值或无法解析时返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_int(value: float) -> Any:
    return int(value) if value.is_integer() else round(value, 2)


def _signed_status(entry: dict[str, Any]) -> str:
    return "已签收" if entry.get("status") == STATUS_ORDER[-1] else "未签收"


def _row_warnings(entry: dict[str, Any]) -> list[str]:
    """逐行业务告警：加注量缺失、计划油量超单次加注上限。"""
    warns: list[str] = []
    if _as_number(entry.get("实际油量")) is None:
        warns.append(WARN_MISSING_ACTUAL)
    planned = _as_number(entry.get("计划油量"))
    if planned is not None and planned > MAX_SINGLE_FUEL_LITERS:
        warns.append(WARN_OVER_LIMIT)
    return warns


def present_row(entry: dict[str, Any], line: int) -> dict[str, Any]:
    """按结算模板口径投影一条记录：补齐车辆/人员/签收状态，现算油量差值。"""
    planned = _as_number(entry.get("计划油量"))
    actual = _as_number(entry.get("实际油量"))
    diff: Any = ""
    if actual is not None and planned is not None:
        diff = _as_int(actual - planned)
    return {
        "行号": line,
        "加油编号": entry.get("加油编号") or "",
        "对应航班": entry.get("对应航班") or "",
        "油料类型": entry.get("油料类型") or "",
        "加油车辆": entry.get("加油车辆") or "",
        "操作人员": entry.get("操作人员") or "",
        "签收状态": _signed_status(entry),
        "计划油量": _as_int(planned) if planned is not None else (entry.get("计划油量") or ""),
        "实际油量": _as_int(actual) if actual is not None else (entry.get("实际油量") or ""),
        "油量差值": diff,
    }


class FuelingService:
    def __init__(self) -> None:
        # 结算生成串行化：两个人同时提交，后到的请求作废旧版本，只留最新一版。
        self._settle_lock = threading.RLock()
        self._job_seq = 0
        self._active_job: dict[str, Any] | None = None
        self._latest_snapshot: dict[str, Any] | None = None

    # ------------------------------------------------------------------ 查询

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
        page_rows = rows[start:start + size]
        line_offset = start
        return [
            self._view_entry(entry, line_offset + index + 1)
            for index, entry in enumerate(page_rows)
        ], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        line = store.rows(MODULE).index(entry) + 1
        return self._view_entry(entry, line)

    def _view_entry(self, entry: dict[str, Any], line: int) -> dict[str, Any]:
        """列表页/详情页共用的读出口：结算字段一并带上，含已签收记录。"""
        view = dict(entry)
        view.update(present_row(entry, line))
        view["status"] = entry.get("status")
        view["异常原因"] = "；".join(_row_warnings(entry))
        return view

    # ------------------------------------------------------------------ 维护

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        carried = [
            *REQUIRED_FIELDS,
            "计划油量",
            "实际油量",
            "加油车辆",
            "操作人员",
        ]
        entry.update({field: values.get(field) for field in carried})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"加油任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于航油加注可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        # 完成加注时可一并回填实际油量，保证清单里的实际油量有来源。
        if values and values.get("实际油量") not in (None, ""):
            entry["实际油量"] = values.get("实际油量")
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"加油任务已{action}"

    # ------------------------------------------------------------ 对账与自检

    def _load_views(self) -> list[dict[str, Any]]:
        """列表页与详情页读到的全量记录（全量、不过滤，含已签收）。"""
        return [self._view_entry(entry, index + 1) for index, entry in enumerate(store.rows(MODULE))]

    def build_audit(self, *, views: list[dict[str, Any]] | None = None, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """重算历史数据条数：列表/详情条数与对账汇总的分状态桶一起算。"""
        if views is None:
            views = self._load_views()
        if rows is None:
            rows = store.rows(MODULE)
        buckets = {status: 0 for status in STATUS_ORDER}
        for row in rows:
            buckets[str(row.get("status"))] = buckets.get(str(row.get("status")), 0) + 1
        return {
            "list_count": len(views),
            "detail_count": len(views),
            "summary_count": sum(buckets.values()),
            "status_buckets": buckets,
        }

    def _cross_check(
        self,
        *,
        views: list[dict[str, Any]] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """三端条数对账。返回 None 表示对得上；否则指出差在哪一行。"""
        views = views if views is not None else self._load_views()
        rows = rows if rows is not None else store.rows(MODULE)
        audit = self.build_audit(views=views, rows=rows)
        counts = {
            "列表页": audit["list_count"],
            "详情页": audit["detail_count"],
            "对账汇总": audit["summary_count"],
        }
        if len(set(counts.values())) != 1:
            return {
                "type": "count_mismatch",
                "line": None,
                "counts": counts,
                "message": (
                    f"三端条数对不上：列表页 {counts['列表页']} 条、"
                    f"详情页 {counts['详情页']} 条、对账汇总 {counts['对账汇总']} 条，已中止出清单"
                ),
            }
        # 条数相同也可能是「签收记录丢失 + 重复行」互相抵消，逐行按编号对一遍。
        detail_ids = [int(view.get("id", 0)) for view in views]
        row_ids = [int(row.get("id", 0)) for row in rows]
        if set(detail_ids) != set(row_ids) or len(detail_ids) != len(set(detail_ids)):
            view_id_set = set(detail_ids)
            for index, row in enumerate(rows, start=1):
                entry_id = int(row.get("id", 0))
                if entry_id not in view_id_set:
                    return {
                        "type": "row_missing",
                        "line": index,
                        "entry_id": entry_id,
                        "加油编号": row.get("加油编号"),
                        "message": f"第 {index} 行（加油编号 {row.get('加油编号')}，状态 {row.get('status')}）在列表/详情页读不到，疑似整条丢失",
                    }
            return {
                "type": "row_duplicate",
                "line": None,
                "message": "列表/详情存在重复行，与对账汇总对不上",
            }
        return None

    # -------------------------------------------------------- 结算清单生成

    def generate_settlement(self, *, resume: bool = False, fail_after_line: int | None = None) -> dict[str, Any]:
        """生成结算清单：先自检，再逐行产出；成功才整版发布。"""
        with self._settle_lock:
            if resume:
                job = self._active_job
                if job is None or job["state"] != "failed":
                    raise SettlementAborted("没有可续算的失败任务，请重新发起生成")
                if job.get("superseded"):
                    raise SettlementAborted("该版本已被更新的生成请求作废，只保留最新一版结果")
                return self._run_job(job, fail_after_line=fail_after_line)

            # 新请求：把进行中/失败的旧任务全部作废，确保只留最新一版。
            if self._active_job is not None:
                self._active_job["superseded"] = True
                self._active_job["state"] = "superseded"
            self._job_seq += 1
            job = {
                "job_id": self._job_seq,
                "version": self._job_seq,
                "state": "processing",
                "superseded": False,
                "next_line": 1,
                "total_lines": 0,
                # 全新生成：不带任何旧任务的暂存行，杜绝串进半份旧数据。
                "staged": [],
                "started_at": _now(),
                "updated_at": _now(),
            }
            self._active_job = job
            return self._run_job(job, fail_after_line=fail_after_line)

    def _run_job(self, job: dict[str, Any], *, fail_after_line: int | None) -> dict[str, Any]:
        if job.get("superseded"):
            raise SettlementAborted("该版本已被更新的生成请求作废，只保留最新一版结果")
        try:
            rows = store.rows(MODULE)
            views = self._load_views()
            job["total_lines"] = len(rows)

            # 续算时数据可能已经增删：先重算历史条数再逐行核对，行号按当前数据定位。
            discrepancy = self._cross_check(views=views, rows=rows)
            if discrepancy is not None:
                return self._fail(job, f"生成已中止：{discrepancy['message']}", discrepancy=discrepancy)

            staged = {int(item["id"]): item for item in job["staged"]}
            next_line = int(job["next_line"])
            for index in range(next_line - 1, len(rows)):
                current_line = index + 1
                entry = rows[index]
                entry_id = int(entry.get("id", 0))
                view = next((candidate for candidate in views if int(candidate.get("id", 0)) == entry_id), None)
                if view is None:
                    discrepancy = {
                        "type": "row_missing",
                        "line": current_line,
                        "entry_id": entry_id,
                        "加油编号": entry.get("加油编号"),
                        "message": f"第 {current_line} 行（加油编号 {entry.get('加油编号')}）在列表/详情页读不到",
                    }
                    return self._fail(job, f"生成已中止：{discrepancy['message']}", line=current_line, discrepancy=discrepancy)

                projected = present_row(entry, current_line)
                staged[entry_id] = {"id": entry_id, **projected}
                # 先把本行结果落进暂存（含失败行），再响应打断：续算从这一行重做，不丢行。
                job["staged"] = list(staged.values())
                job["updated_at"] = _now()

                # 模拟中途打断：只允许本地/测试环境注入，正式环境忽略该参数。
                # next_line 停在当前行——续算时从失败的这一行重新处理，不跳过也不留半行。
                if fail_after_line is not None and current_line == fail_after_line and settings.env in ("local", "test"):
                    job["next_line"] = current_line
                    raise SettlementAborted(
                        f"生成在第 {current_line} 行被打断，可从该行继续",
                        line=current_line,
                        discrepancy={"type": "interrupted", "line": current_line, "message": "生成过程中途打断"},
                    )
                job["next_line"] = current_line + 1

            snapshot = self._publish(job, rows, staged)
            return self._ok_view(job, snapshot)
        except SettlementAborted as exc:
            return self._fail(job, str(exc), line=exc.line, discrepancy=exc.discrepancy)

    def _publish(self, job: dict[str, Any], rows: list[dict[str, Any]], staged: dict[int, dict[str, Any]]) -> dict[str, Any]:
        """整版发布：按当前行号排序、挂上告警原因，替换掉历史版本（不留半份）。"""
        warnings_by_id = {int(row.get("id", 0)): _row_warnings(row) for row in rows}
        line_of = {int(row.get("id", 0)): index + 1 for index, row in enumerate(rows)}
        items: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        # 续算期间数据可能增删：行号一律按当前顺序重算，避免沿用中断前的旧行号。
        for row in rows:
            entry_id = int(row.get("id", 0))
            if entry_id not in staged:
                continue
            staged_item = staged[entry_id]
            warns = warnings_by_id.get(entry_id, [])
            # 严格按模板列顺序重建，id 只用于内部定位，不进结算清单。
            item = {column: staged_item.get(column, "") for column in SETTLEMENT_COLUMNS}
            item["行号"] = line_of[entry_id]
            if warns:
                item["异常原因"] = "；".join(warns)
                warnings.append({"行号": item["行号"], "加油编号": item["加油编号"], "原因": warns})
            items.append(item)
        snapshot = {
            "version": job["version"],
            "generated_at": _now(),
            "columns": list(SETTLEMENT_COLUMNS) + ["异常原因"],
            "total": len(items),
            "items": items,
            "warnings": warnings,
            "audit": self.build_audit(),
        }
        job["state"] = "completed"
        job["next_line"] = job["total_lines"] + 1
        job["staged"] = []
        job["updated_at"] = snapshot["generated_at"]
        # 只留最新一版结果：新快照直接替换旧快照，旧任务标记作废。
        self._latest_snapshot = snapshot
        self._active_job = None
        return snapshot

    def _staged_warnings(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        """已暂存行里的告警（行号按当前数据顺序重算）。"""
        rows = store.rows(MODULE)
        line_of = {int(row.get("id", 0)): index + 1 for index, row in enumerate(rows)}
        warns_by_id = {int(row.get("id", 0)): _row_warnings(row) for row in rows}
        result = []
        for item in job.get("staged", []):
            entry_id = int(item["id"])
            warns = warns_by_id.get(entry_id, [])
            if warns:
                result.append({"行号": line_of.get(entry_id, item["行号"]), "加油编号": item["加油编号"], "原因": warns})
        return result

    def _fail(self, job: dict[str, Any], message: str, *, line: int | None = None, discrepancy: dict[str, Any] | None = None) -> dict[str, Any]:
        job["state"] = "failed"
        job["failed_line"] = line
        job["discrepancy"] = discrepancy
        job["updated_at"] = _now()
        return {
            "ok": False,
            "status": "failed",
            "message": message,
            "job_id": job["job_id"],
            "version": job["version"],
            "total_lines": job["total_lines"],
            "failed_line": job.get("next_line") if line is None else line,
            "next_line": job["next_line"],
            "discrepancy": discrepancy,
            "warnings": self._staged_warnings(job),
            "snapshot": None,
            "latest": copy.deepcopy(self._latest_snapshot),
            "superseded": bool(job.get("superseded")),
        }

    def _ok_view(self, job: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "status": "completed",
            "message": f"结算清单已生成，共 {snapshot['total']} 行；历史条数已重算",
            "job_id": job["job_id"],
            "version": job["version"],
            "total_lines": snapshot["total"],
            "failed_line": None,
            "next_line": None,
            "discrepancy": None,
            "warnings": snapshot["warnings"],
            "snapshot": snapshot,
            "latest": copy.deepcopy(snapshot),
            "superseded": False,
        }

    def settlement_state(self) -> dict[str, Any]:
        """供页面查询：当前任务进度（若有）+ 最新一版已发布清单 + 实时重算条数。"""
        with self._settle_lock:
            live_audit = self.build_audit()
            if self._active_job is not None:
                job = self._active_job
                return {
                    "ok": False,
                    "status": job["state"],
                    "message": (
                        f"清单生成在第 {job.get('next_line', 1)} 行中止，修复差异后可从该行续算"
                        if job["state"] == "failed"
                        else "清单生成进行中，已有更新的请求会作废旧版本"
                        if job["state"] == "superseded"
                        else "清单生成进行中"
                    ),
                    "job_id": job["job_id"],
                    "version": job["version"],
                    "total_lines": job["total_lines"],
                    "failed_line": job.get("next_line"),
                    "next_line": job.get("next_line"),
                    "discrepancy": job.get("discrepancy"),
                    "warnings": self._staged_warnings(job),
                    "snapshot": None,
                    "latest": copy.deepcopy(self._latest_snapshot),
                    "live_audit": live_audit,
                    "superseded": bool(job.get("superseded")),
                }
            latest = copy.deepcopy(self._latest_snapshot)
            if latest is None:
                return {
                    "ok": False,
                    "status": "idle",
                    "message": "尚未生成过结算清单",
                    "job_id": None,
                    "version": None,
                    "total_lines": live_audit["list_count"],
                    "failed_line": None,
                    "next_line": None,
                    "discrepancy": None,
                    "warnings": [],
                    "snapshot": None,
                    "latest": None,
                    "live_audit": live_audit,
                    "superseded": False,
                }
            return {
                "ok": True,
                "status": "completed",
                "message": f"当前为第 {latest['version']} 版结算清单（最新）",
                "job_id": None,
                "version": latest["version"],
                "total_lines": latest["total"],
                "failed_line": None,
                "next_line": None,
                "discrepancy": None,
                "warnings": latest.get("warnings", []),
                "snapshot": latest,
                "latest": latest,
                "live_audit": live_audit,
                "superseded": False,
            }
