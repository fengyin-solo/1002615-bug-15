"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class SettlementPayload(BaseModel):
    """结算清单生成请求。

    resume=true 表示上次生成中途失败，从记录的失败行继续；
    fail_after_line 只用于本地/测试环境模拟「中途打断」。
    """

    resume: bool = False
    fail_after_line: int | None = None


class SettlementView(BaseModel):
    """结算清单生成结果：状态、差异说明与最新一版清单都在这里。"""

    ok: bool
    status: str
    message: str
    job_id: int | None = None
    version: int | None = None
    total_lines: int = 0
    failed_line: int | None = None
    next_line: int | None = None
    discrepancy: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    snapshot: dict[str, Any] | None = None
    latest: dict[str, Any] | None = None
    live_audit: dict[str, Any] | None = None
    superseded: bool = False



class FlightstandEntry(BaseModel):
    """机位明细结构。"""

    field_0: str | None = None  # 机位编号
    field_1: str | None = None  # 机位类型
    field_2: str | None = None  # 可停机型
    field_3: str | None = None  # 廊桥配置
    field_4: str | None = None  # 引导线状态
    field_5: str | None = None  # 占用状态
    field_6: str | None = None  # 分配航段
    field_7: str | None = None  # 机位状态

class MarshallingEntry(BaseModel):
    """引导任务明细结构。"""

    field_0: str | None = None  # 引导编号
    field_1: str | None = None  # 对应航班
    field_2: str | None = None  # 机位编号
    field_3: str | None = None  # 引导车编号
    field_4: str | None = None  # 引导员
    field_5: str | None = None  # 预计到位
    field_6: str | None = None  # 实际到位
    field_7: str | None = None  # 引导状态

class BridgeEntry(BaseModel):
    """廊桥明细结构。"""

    field_0: str | None = None  # 廊桥编号
    field_1: str | None = None  # 对应机位
    field_2: str | None = None  # 适用机型
    field_3: str | None = None  # 对接高度
    field_4: str | None = None  # 预靠时间
    field_5: str | None = None  # 撤桥时间
    field_6: str | None = None  # 操作人员
    field_7: str | None = None  # 廊桥状态

class BaggageEntry(BaseModel):
    """行李任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 对应航班
    field_2: str | None = None  # 行李类型
    field_3: str | None = None  # 装卸方向
    field_4: str | None = None  # 出发转盘
    field_5: str | None = None  # 到达转盘
    field_6: str | None = None  # 装卸班组
    field_7: str | None = None  # 任务状态

class CateringEntry(BaseModel):
    """配餐任务明细结构。"""

    field_0: str | None = None  # 配餐编号
    field_1: str | None = None  # 对应航班
    field_2: str | None = None  # 餐食类型
    field_3: str | None = None  # 餐食数量
    field_4: str | None = None  # 配送车辆
    field_5: str | None = None  # 配送人员
    field_6: str | None = None  # 预计送达
    field_7: str | None = None  # 配餐状态

class FuelingEntry(BaseModel):
    """加油任务明细结构。"""

    field_0: str | None = None  # 加油编号
    field_1: str | None = None  # 对应航班
    field_2: str | None = None  # 油料类型
    field_3: str | None = None  # 计划油量
    field_4: str | None = None  # 实际油量
    field_5: str | None = None  # 加油车辆
    field_6: str | None = None  # 操作人员
    field_7: str | None = None  # 加油状态

class DeicingEntry(BaseModel):
    """除冰任务明细结构。"""

    field_0: str | None = None  # 除冰编号
    field_1: str | None = None  # 对应航班
    field_2: str | None = None  # 除冰液类型
    field_3: str | None = None  # 喷洒量
    field_4: str | None = None  # 作业车辆
    field_5: str | None = None  # 作业人员
    field_6: str | None = None  # 作业区域
    field_7: str | None = None  # 除冰状态

class LavatoryEntry(BaseModel):
    """排污任务明细结构。"""

    field_0: str | None = None  # 排污编号
    field_1: str | None = None  # 对应航班
    field_2: str | None = None  # 清水加注量
    field_3: str | None = None  # 排污量
    field_4: str | None = None  # 服务车辆
    field_5: str | None = None  # 操作人员
    field_6: str | None = None  # 完成时间
    field_7: str | None = None  # 服务状态

class PushbackEntry(BaseModel):
    """推出任务明细结构。"""

    field_0: str | None = None  # 推出编号
    field_1: str | None = None  # 对应航班
    field_2: str | None = None  # 推出方向
    field_3: str | None = None  # 牵引车编号
    field_4: str | None = None  # 牵引车司机
    field_5: str | None = None  # 通信频道
    field_6: str | None = None  # 推出时段
    field_7: str | None = None  # 推出状态

class GseEntry(BaseModel):
    """地面设备明细结构。"""

    field_0: str | None = None  # 设备编号
    field_1: str | None = None  # 设备类型
    field_2: str | None = None  # 所属区域
    field_3: str | None = None  # 适用机型
    field_4: str | None = None  # 设备年限
    field_5: str | None = None  # 上次检修
    field_6: str | None = None  # 下次检修日
    field_7: str | None = None  # 设备状态

class CargoEntry(BaseModel):
    """货邮任务明细结构。"""

    field_0: str | None = None  # 货邮编号
    field_1: str | None = None  # 对应航班
    field_2: str | None = None  # 货物类型
    field_3: str | None = None  # 总重吨位
    field_4: str | None = None  # 板箱数量
    field_5: str | None = None  # 装卸班组
    field_6: str | None = None  # 舱位分配
    field_7: str | None = None  # 装卸状态

class ClearanceEntry(BaseModel):
    """放行记录明细结构。"""

    field_0: str | None = None  # 放行编号
    field_1: str | None = None  # 对应航班
    field_2: str | None = None  # 签派员
    field_3: str | None = None  # 放行条件
    field_4: str | None = None  # 燃油确认
    field_5: str | None = None  # 载重平衡
    field_6: str | None = None  # 技术放行
    field_7: str | None = None  # 放行状态

class TurnaroundEntry(BaseModel):
    """过站任务明细结构。"""

    field_0: str | None = None  # 过站编号
    field_1: str | None = None  # 对应航班
    field_2: str | None = None  # 计划过站时间
    field_3: str | None = None  # 实际过站时间
    field_4: str | None = None  # 过站延误
    field_5: str | None = None  # 延误原因
    field_6: str | None = None  # 协调记录
    field_7: str | None = None  # 过站状态

class RampEntry(BaseModel):
    """巡查记录明细结构。"""

    field_0: str | None = None  # 巡查编号
    field_1: str | None = None  # 巡查区域
    field_2: str | None = None  # 巡查日期
    field_3: str | None = None  # 巡查人员
    field_4: str | None = None  # 发现隐患
    field_5: str | None = None  # 处置措施
    field_6: str | None = None  # 复查结果
    field_7: str | None = None  # 巡查状态

class Weather2Entry(BaseModel):
    """气象观测明细结构。"""

    field_0: str | None = None  # 观测编号
    field_1: str | None = None  # 观测时刻
    field_2: str | None = None  # 能见度
    field_3: str | None = None  # 云底高
    field_4: str | None = None  # 风向风速
    field_5: str | None = None  # 跑道视程
    field_6: str | None = None  # 气象观测

class VehicleEntry(BaseModel):
    """特种车辆明细结构。"""

    field_0: str | None = None  # 车辆编号
    field_1: str | None = None  # 车辆类型
    field_2: str | None = None  # 所属车队
    field_3: str | None = None  # 车辆状态
    field_4: str | None = None  # 年检日期
    field_5: str | None = None  # 驾驶员
    field_6: str | None = None  # 燃油量
    field_7: str | None = None  # 调度状态

class StaffshiftEntry(BaseModel):
    """排班记录明细结构。"""

    field_0: str | None = None  # 排班编号
    field_1: str | None = None  # 岗位名称
    field_2: str | None = None  # 值班人员
    field_3: str | None = None  # 值班日期
    field_4: str | None = None  # 班次时段
    field_5: str | None = None  # 替班人员
    field_6: str | None = None  # 到岗确认
    field_7: str | None = None  # 排班状态

class RunwayEntry(BaseModel):
    """助航灯光明细结构。"""

    field_0: str | None = None  # 灯光编号
    field_1: str | None = None  # 灯光类型
    field_2: str | None = None  # 所在位置
    field_3: str | None = None  # 光级档位
    field_4: str | None = None  # 回路电流
    field_5: str | None = None  # 监控状态
    field_6: str | None = None  # 故障报警
    field_7: str | None = None  # 灯光状态

class EmergencyplanEntry(BaseModel):
    """应急预案明细结构。"""

    field_0: str | None = None  # 预案编号
    field_1: str | None = None  # 预案名称
    field_2: str | None = None  # 适用场景
    field_3: str | None = None  # 响应等级
    field_4: str | None = None  # 指挥岗位
    field_5: str | None = None  # 处置流程
    field_6: str | None = None  # 演练日期
    field_7: str | None = None  # 预案状态

class QualitycheckEntry(BaseModel):
    """监察记录明细结构。"""

    field_0: str | None = None  # 监察编号
    field_1: str | None = None  # 监察日期
    field_2: str | None = None  # 监察区域
    field_3: str | None = None  # 监察事项
    field_4: str | None = None  # 发现违章
    field_5: str | None = None  # 整改要求
    field_6: str | None = None  # 整改期限
    field_7: str | None = None  # 监察状态
