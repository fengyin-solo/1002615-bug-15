# 机场地面保障管理平台

面向机场地面保障的航空器引导、客梯对接、行李装卸、航油加注、除冰作业与廊桥调度的一体化管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 机位分配 | `flightstand` | 机位 | 机位编号、机位类型、可停机型 |
| 引导入位 | `marshalling` | 引导任务 | 引导编号、对应航班、机位编号 |
| 廊桥对接 | `bridge` | 廊桥 | 廊桥编号、对应机位、适用机型 |
| 行李装卸 | `baggage` | 行李任务 | 任务编号、对应航班、行李类型 |
| 航食配餐 | `catering` | 配餐任务 | 配餐编号、对应航班、餐食类型 |
| 航油加注 | `fueling` | 加油任务 | 加油编号、对应航班、油料类型 |
| 除冰作业 | `deicing` | 除冰任务 | 除冰编号、对应航班、除冰液类型 |
| 清水排污 | `lavatory` | 排污任务 | 排污编号、对应航班、清水加注量 |
| 推出开车 | `pushback` | 推出任务 | 推出编号、对应航班、推出方向 |
| 地面设备 | `gse` | 地面设备 | 设备编号、设备类型、所属区域 |
| 货物装卸 | `cargo` | 货邮任务 | 货邮编号、对应航班、货物类型 |
| 放行签派 | `clearance` | 放行记录 | 放行编号、对应航班、签派员 |
| 过站保障 | `turnaround` | 过站任务 | 过站编号、对应航班、计划过站时间 |
| 机坪巡查 | `ramp` | 巡查记录 | 巡查编号、巡查区域、巡查日期 |
| 航空气象 | `weather2` | 气象观测 | 观测编号、观测时刻、能见度 |
| 特种车辆 | `vehicle` | 特种车辆 | 车辆编号、车辆类型、所属车队 |
| 人员排班 | `staffshift` | 排班记录 | 排班编号、岗位名称、值班人员 |
| 跑道灯光 | `runway` | 助航灯光 | 灯光编号、灯光类型、所在位置 |
| 应急处置 | `emergencyplan` | 应急预案 | 预案编号、预案名称、适用场景 |
| 质量监察 | `qualitycheck` | 监察记录 | 监察编号、监察日期、监察区域 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 航油加注结算清单出单自检

结算清单（`POST /api/fueling/settlement/generate`）出单前先自检，任一项不通过即中止、
不落半份清单，响应里指出差在哪一行（`issues[].row`、加油编号与原因）：

- 每行必须带齐加油车辆、油料类型、实际油量（加注量）、签收状态、操作人员；
- 计划/实际油量要能解析成数值，计划油量超过单次加注上限（50000 升）的行列出原因；
- 列表页、详情页与对账汇总三处条数必须一致（含已签收记录，防止整条丢失）；
- 表头固定按结算模板顺序：加油编号、对应航班、油料类型、加油车辆、操作人员、
  计划油量、实际油量、油量差值（实际-计划）、签收状态。

中止后修正差异行（`PATCH /api/fueling/{id}` 补录），再以
`POST /api/fueling/settlement/generate?resume=true` 从失败行继续自检；全部通过才一次性
发布清单。每次出单都按当前数据重算历史版本条数；并发提交按版本号只保留最新一版，
被取代的请求返回 `status=superseded`。`GET /api/fueling/settlement` 查询最近一版结果与
中止现场，`GET /api/fueling/export` 兼容旧导出入口。
