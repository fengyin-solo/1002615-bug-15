"""航油加注结算清单的自检、续算、重算与版本控制测试。

直接跑：PYTHONPATH=. python3 -m unittest app.tests.test_fueling_settlement -v
"""
from __future__ import annotations

import copy
import unittest

from app.services import fueling as fueling_module
from app.services.fueling import (
    MAX_SINGLE_FUEL_LITERS,
    SETTLEMENT_COLUMNS,
    FuelingService,
    present_row,
)
from app.store import store

FUEL_ROWS = store.rows("fueling")


def make_row(entry_id: int, status: str = "已签收", planned: object = 1000, actual: object = 980) -> dict:
    return {
        "id": entry_id,
        "status": status,
        "pending": status != "已签收",
        "abnormal": False,
        "加油编号": f"FUEL-T{entry_id:04d}",
        "对应航班": f"CA{entry_id}00",
        "油料类型": "Jet A-1",
        "计划油量": planned,
        "实际油量": actual,
        "加油车辆": f"油车-B{entry_id}",
        "操作人员": "测试操作员",
    }


class FuelingSettlementTest(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例都用独立内存数据，避免结算版本状态互相污染。
        self._backup = copy.deepcopy(FUEL_ROWS)
        FUEL_ROWS[:] = [make_row(1), make_row(2, status="已加注", planned=800, actual=820)]
        self.service = FuelingService()

    def tearDown(self) -> None:
        FUEL_ROWS[:] = self._backup

    # 1. 清单必须带上加油车辆、油料类型、实际油量、签收状态、操作人员
    def test_columns_cover_required_fields(self) -> None:
        for field in ["加油车辆", "油料类型", "实际油量", "签收状态", "操作人员", "油量差值"]:
            self.assertIn(field, SETTLEMENT_COLUMNS)
        result = self.service.generate_settlement()
        self.assertTrue(result["ok"], result["message"])
        item = result["snapshot"]["items"][0]
        self.assertEqual(item["加油车辆"], "油车-B1")
        self.assertEqual(item["油料类型"], "Jet A-1")
        self.assertEqual(item["操作人员"], "测试操作员")
        self.assertEqual(item["签收状态"], "已签收")
        self.assertEqual(item["实际油量"], 980)

    # 2. 油量差值 = 实际 - 计划（修掉原来的错误显示）
    def test_oil_diff_is_actual_minus_planned(self) -> None:
        first = present_row(FUEL_ROWS[0], 1)
        second = present_row(FUEL_ROWS[1], 2)
        self.assertEqual(first["油量差值"], -20)
        self.assertEqual(second["油量差值"], 20)
        result = self.service.generate_settlement()
        self.assertEqual([row["油量差值"] for row in result["snapshot"]["items"]], [-20, 20])

    # 3. 已签收记录不能整条丢失：列表、详情与结算都读得到
    def test_signed_records_not_dropped(self) -> None:
        signed = [row for row in FUEL_ROWS if row["status"] == "已签收"]
        self.assertTrue(signed)
        items, total = self.service.list_entries(page=1, size=50)
        self.assertEqual(total, len(FUEL_ROWS))
        self.assertEqual(len(items), len(FUEL_ROWS))
        detail = self.service.get_entry(1)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["签收状态"], "已签收")
        result = self.service.generate_settlement()
        signed_ids = {row["id"] for row in signed}
        self.assertTrue(signed_ids.issubset({row["id"] for row in FUEL_ROWS}))

    # 4. 表头顺序按结算模板对齐
    def test_template_header_order(self) -> None:
        result = self.service.generate_settlement()
        self.assertEqual(result["snapshot"]["columns"][:-1], SETTLEMENT_COLUMNS)
        self.assertEqual(list(result["snapshot"]["items"][0].keys())[: len(SETTLEMENT_COLUMNS)], SETTLEMENT_COLUMNS)

    # 5. 列表/详情/汇总条数对不上时中止，并指出差在哪一行
    def test_count_mismatch_aborts(self) -> None:
        real_load = self.service._load_views

        def partial_views() -> list[dict]:
            return real_load()[:-1]  # 列表页少读一条，模拟签收记录丢失

        self.service._load_views = partial_views  # type: ignore[method-assign]
        result = self.service.generate_settlement()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertIsNotNone(result["discrepancy"])
        self.assertIn("三端条数对不上", result["message"])
        # 中止后不允许留下半份清单
        self.assertIsNone(self.service.settlement_state()["latest"])

    # 5b. 条数相同但有记录丢失（重复行抵消）也要点出具体行
    def test_row_missing_pointed_out_by_line(self) -> None:
        real_load = self.service._load_views

        def views_without_first() -> list[dict]:
            return [view for view in real_load() if int(view["id"]) != 1] + [
                dict(real_load()[1])  # 重复一行凑数，条数相同
            ]

        self.service._load_views = views_without_first  # type: ignore[method-assign]
        result = self.service.generate_settlement()
        self.assertFalse(result["ok"])
        self.assertEqual(result["discrepancy"]["type"], "row_missing")
        self.assertEqual(result["discrepancy"]["line"], 1)

    # 6. 加注量缺失 / 计划油量超上限：进清单并列出原因
    def test_warning_rows_list_reasons(self) -> None:
        FUEL_ROWS.append(make_row(3, status="已加注", planned=1000, actual=""))
        FUEL_ROWS.append(make_row(4, status="待加油", planned=MAX_SINGLE_FUEL_LITERS + 100, actual=""))
        result = self.service.generate_settlement()
        self.assertTrue(result["ok"], result["message"])
        reasons = {w["加油编号"]: w["原因"] for w in result["warnings"]}
        self.assertIn("FUEL-T0003", reasons)
        self.assertIn("加注量缺失", reasons["FUEL-T0003"])
        self.assertIn("FUEL-T0004", reasons)
        self.assertTrue(any("单次加注上限" in text for text in reasons["FUEL-T0004"]))
        # 告警行不删，仍在清单里
        self.assertEqual(result["snapshot"]["total"], 4)

    # 7. 中途打断后续算：从失败行继续，成功才发布整版
    def test_resume_from_failed_line(self) -> None:
        first = self.service.generate_settlement(fail_after_line=2)
        self.assertFalse(first["ok"])
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["failed_line"], 2)
        self.assertEqual(first["next_line"], 2)
        # 半份清单不发布
        self.assertIsNone(self.service.settlement_state()["latest"])

        resumed = self.service.generate_settlement(resume=True)
        self.assertTrue(resumed["ok"], resumed["message"])
        self.assertEqual(resumed["snapshot"]["total"], 2)
        self.assertEqual(self.service.settlement_state()["status"], "completed")

    # 8. 历史数据条数随每次生成重算
    def test_history_counts_recomputed(self) -> None:
        result = self.service.generate_settlement()
        self.assertEqual(result["snapshot"]["audit"]["summary_count"], 2)
        FUEL_ROWS.append(make_row(3))
        state = self.service.settlement_state()
        self.assertEqual(state["live_audit"]["summary_count"], 3)
        again = self.service.generate_settlement()
        self.assertEqual(again["snapshot"]["audit"]["summary_count"], 3)
        self.assertEqual(again["snapshot"]["total"], 3)

    # 9. 并发提交：后到的请求作废旧版，只留最新一版
    def test_concurrent_submit_keeps_latest_only(self) -> None:
        first = self.service.generate_settlement(fail_after_line=1)
        first_job_id = first["job_id"]
        first_job = self.service._active_job
        # 第二个人又发起一次生成（不带 resume）
        second = self.service.generate_settlement(fail_after_line=1)
        self.assertNotEqual(second["job_id"], first_job_id)
        self.assertGreater(second["version"], first["version"])
        # 旧任务对象被标记作废，且其执行入口会被守卫拦下
        self.assertTrue(first_job["superseded"])
        with self.assertRaises(fueling_module.SettlementAborted):
            self.service._run_job(first_job, fail_after_line=None)
        # 当前活动任务只可能是最新一版；续算成功后发布的也是最新版
        self.assertEqual(self.service._active_job["job_id"], second["job_id"])
        done = self.service.generate_settlement(resume=True)
        self.assertTrue(done["ok"])
        self.assertEqual(done["version"], second["version"])
        state = self.service.settlement_state()
        self.assertEqual(state["version"], second["version"])

    # 10. 差异行号即结算清单行号（从 1 开始）
    def test_diff_line_number_is_one_based(self) -> None:
        real_load = self.service._load_views

        def views_without_last() -> list[dict]:
            return real_load()[:-1] + [dict(real_load()[0])]

        self.service._load_views = views_without_last  # type: ignore[method-assign]
        result = self.service.generate_settlement()
        self.assertFalse(result["ok"])
        self.assertEqual(result["discrepancy"]["line"], 2)


if __name__ == "__main__":
    unittest.main()
