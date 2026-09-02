# Mock Teamcenter（mocktc）

轻量级 **Siemens Teamcenter 模拟系统**，仅用于接口联调测试。为外部系统（如 ECC）
提供 Teamcenter 风格的 RESTful BOM 接口，内置简单界面、接口日志查看器和示例 BOM 数据。
部署地址：`https://mocktc.bjlzc.cn`（DNS 配置后生效）。

> 这不是真实的 Siemens Teamcenter，不包含任何西门子授权组件，仅模拟常见接口形态。

## 功能

- RESTful 接口：物料（Items）、版本（Revisions）、BOM 结构（单层/多级展开）
- BOM JSON 查询与维护 API：fixture 列表、完整读取、按物料/字段查询，以及受控增删改
- 一键导出：全部物料、标准 BOM 和外部 BOM 合并为一个 Excel；单数据集可下载原始 JSON
- 内置示例 BOM 数据（3 个总成、8 个 BOM 头、24 行 BOM 行、21 个物料）
- 接口日志：自动记录每次 `/tc/v1/*` 调用的请求/响应、耗时、状态码，界面实时查看
- 简单界面：首页 / API 文档 / 接口日志 / 数据浏览与 BOM 编辑
- 可选 Token 鉴权（环境变量 `MOCKTC_API_TOKEN`，默认关闭）
- 数据持久化到 SQLite，重启不丢失

## 接口一览

基础路径 `/tc/v1`，返回统一为 `{"status": <code>, "message": "...", "data": {...}}`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/tc/v1/health` | 健康检查 |
| GET | `/tc/v1/items` | 物料列表/搜索（`item_id`、`q`、`item_type`、`project`、`status`、`limit`、`offset`） |
| POST | `/tc/v1/items` | 创建物料 |
| GET | `/tc/v1/items/<uid>` | 物料详情 |
| GET | `/tc/v1/items/<uid>/revisions` | 版本列表 |
| GET | `/tc/v1/items/<uid>/revisions/<rev_uid>` | 版本详情 |
| GET | `/tc/v1/items/<uid>/bom` | BOM（`depth=0` 单层，`depth=-1` 全展开） |
| GET | `/tc/v1/items/<uid>/bom/expand` | BOM 全展开 |
| GET | `/tc/v1/structures/<item_uid>` | BOM 结构别名接口 |
| GET | `/tc/v1/bomlines/<uid>` | BOM 行详情 |
| GET | `/tc/v1/bomlines/<uid>/children` | BOM 行子行 |
| GET | `/tc/v1/fixtures` | 只读：已加载的 BOM JSON fixture 列表 |
| GET | `/tc/v1/fixtures/<name>` | 只读：完整 fixture 读取（`?raw=1` 返回原始字节） |
| GET | `/tc/v1/fixtures/<name>/query` | 只读：按物料/字段查询（`part_id`、`part_name`、`q`、`bom_level`、`revision_id`、`parent_id`、`child_uid`、`parent_uid`、`exact`、`limit`、`offset`） |
| GET | `/tc/v1/fixtures/<name>/materials/<part_id>` | 只读：物料详情（不存在返回 404） |
| GET | `/tc/v1/export.xlsx` | 下载全部MockTC数据的Excel工作簿 |
| GET | `/tc/v1/fixtures/<name>/download` | 下载单个fixture原始JSON |
| POST | `/tc/v1/fixtures/import` | 管理：导入新 BOM JSON 数据集（multipart `file`/`name`，同名拒绝覆盖） |
| PATCH | `/tc/v1/fixtures/<name>/rows/<child_uid>` | 管理：修改 fixture BOM 节点 |
| POST | `/tc/v1/fixtures/<name>/rows` | 管理：新增 fixture BOM 子节点 |
| DELETE | `/tc/v1/fixtures/<name>/rows/<child_uid>` | 管理：删除节点；有下级时须 `cascade=1` |
| PATCH | `/tc/v1/bomlines/<uid>` | 管理：修改标准 BOM 行 |
| POST | `/tc/v1/items/<uid>/bomlines` | 管理：新增标准 BOM 行 |
| DELETE | `/tc/v1/bomlines/<uid>` | 管理：删除标准 BOM 行 |

## BOM 数据维护

打开 `https://mocktc.bjlzc.cn/data` 可查看标准 BOM 和所有外部 fixture。外部数据集
支持任意字段搜索、分页、编辑、新增下级和级联删除；标准 BOM 详情页支持组件增删改。
页面顶部的“一键下载全部数据”会生成一个包含导出说明、物料清单、标准 BOM 以及每个
外部 BOM 数据集独立工作表的 `.xlsx` 文件。维护页还可导入 UTF-8 JSON
数组为全新数据集；同名时返回 409，不提供覆盖开关。导入限制为 2 MiB、
20000 行，并校验唯一根节点、唯一 `child_uid`、父子层级和标量字段。

所有写操作都必须通过请求头 `X-MockTC-Admin-Token` 提交管理员令牌。服务从
`MOCKTC_ADMIN_TOKEN` 环境变量读取真实值；ECC 运行环境将其保存在权限为 `0600` 的
`/oracle/mocktc/.env`，前端只在当前浏览器的 `sessionStorage` 中暂存用户输入，服务器
不会把令牌返回页面或日志。

fixture 修改前会在 `fixtures/.history/` 自动创建原始文件快照，然后使用文件锁和原子
替换落盘；SQLite 标准 BOM 修改前会在 `data/.history/` 创建数据库快照。因此页面编辑
失败不会留下半写文件，历史版本也可用于人工回退。

### 正式环境 fixture 对拍与显式同步

`scripts/sync_production_fixtures.py` 仅处理外部 JSON fixture，不会同步 SQLite
标准物料/BOM。默认是只读对拍：读取正式 MockTC 的 fixture 清单和原始 JSON，输出
生产与麒麟的行数、哈希和状态，不创建文件、不写审计记录。

```sh
python3.11 scripts/sync_production_fixtures.py \
  --target-dir /var/lib/xiaogang/mocktc/fixtures
```

替换必须由运维人员明确指定**一个** fixture，且必须提供替换前目标文件的 SHA-256；新建
fixture 时只接受 `NAME=absent`。程序以与服务相同的 `.mocktc-fixtures.lock` 加锁，
先将旧文件保存到 `.history/`，再同目录原子替换，并在 `.sync-history/` 写入不含令牌
的审计 manifest。没有“同步全部”或自动覆盖开关。

```sh
python3.11 scripts/sync_production_fixtures.py \
  --target-dir /var/lib/xiaogang/mocktc/fixtures --apply \
  --fixture 20260810-sap-alignment-diff-G100000013.json \
  --expect-target-sha 20260810-sap-alignment-diff-G100000013.json=<对拍报告中的目标SHA256>
```

### 外部 BOM 数据（LITHO-001）

物料 `LITHO-001`（光刻机整机）的 BOM 接口直接返回外部上传文件
`mocktc_app/fixtures/20260808-bom1-2.json` 的原始内容（3316 行，顶层为数组，
字段 `bom_level` / `parent_uid` / `child_uid` / `part_id` / `revision_id` /
`part_name` / `quantity`），不做 JSON 包装：

```sh
curl https://mocktc.bjlzc.cn/tc/v1/items/item-litho-001/bom
curl https://mocktc.bjlzc.cn/tc/v1/items/item-litho-001/bom/expand
curl https://mocktc.bjlzc.cn/tc/v1/structures/item-litho-001
```

常用示例：

```sh
curl https://mocktc.bjlzc.cn/tc/v1/health
curl "https://mocktc.bjlzc.cn/tc/v1/items?q=变速箱"
curl "https://mocktc.bjlzc.cn/tc/v1/items/item-p1000/bom?depth=0"
curl "https://mocktc.bjlzc.cn/tc/v1/items/item-p1000/bom/expand"
```

### 只读 BOM JSON 查询 API（供 TC 物料查询技能使用）

fixture 文件在启动时加载进内存（默认目录 `mocktc_app/fixtures/`，可用环境变量
`MOCKTC_FIXTURE_DIR` 覆盖），所有 `/tc/v1/fixtures*` 接口均为只读、稳定返回
统一包装 `{"status": 200, "message": "OK", "data": {...}}`；未知 fixture / 物料
返回 404，非法参数（`limit`/`offset`/`bom_level` 非整数等）返回 400。

```sh
# 1. 查看可用 fixture（元数据：行数、字段、根物料）
curl https://mocktc.bjlzc.cn/tc/v1/fixtures

# 2. 完整读取 fixture（默认结构化包装；?raw=1 返回与文件完全一致的原始 JSON 数组）
curl https://mocktc.bjlzc.cn/tc/v1/fixtures/20260808-bom1-2.json
curl https://mocktc.bjlzc.cn/tc/v1/fixtures/20260808-bom1-2.json?raw=1

# 3. 按物料编号查询（part_id 默认模糊匹配，exact=1 精确匹配；返回行附带 parent_id/parent_name）
curl "https://mocktc.bjlzc.cn/tc/v1/fixtures/20260808-bom1-2.json/query?part_id=S01&exact=1"
curl "https://mocktc.bjlzc.cn/tc/v1/fixtures/20260808-bom1-2.json/query?q=光源"
curl "https://mocktc.bjlzc.cn/tc/v1/fixtures/20260808-bom1-2.json/query?bom_level=1"
curl "https://mocktc.bjlzc.cn/tc/v1/fixtures/20260808-bom1-2.json/query?parent_id=LITHO-001"
curl "https://mocktc.bjlzc.cn/tc/v1/fixtures/20260808-bom1-2.json/query?limit=50&offset=0"

# 4. 物料详情（不存在返回 404）
curl https://mocktc.bjlzc.cn/tc/v1/fixtures/20260808-bom1-2.json/materials/S01
```

`query` 接口响应示例（`data.items` 中每行在原字段基础上补充
`parent_id` / `parent_name`，用于定位该物料所属父级）：

```json
{"status":200,"message":"OK","data":{
  "fixture":{"name":"20260808-bom1-2.json","rows":3316},
  "total":1,"limit":200,"offset":0,
  "items":[{"bom_level":1,"parent_uid":"wompmV_gJj7wdB","child_uid":"AJjpmV_gJj7wdB",
            "part_id":"S01","revision_id":"A","part_name":"光源系统","quantity":1,
            "parent_id":"LITHO-001","parent_name":"光刻机整机"}]}}
```

### ECC/TC 差异测试 fixture（SAP 对齐）

`20260810-sap-alignment-diff-G100000013.json`（20 行）是供 ECC/TC 差异测试的确定性
TC 侧 fixture：顶层物料 `G100000013`，BOM 头 `00000011/01`，基本数量 `1000`（单位 EA），
结构以 ECC（CS_BOM_EXPL_MAT_V2 未限定工厂完整展开，18 行组件、最大 5 层）为基线对齐，
保留各层 BOM 编号、项目号与父子关系，并完整保留 ECC 侧的部件损耗率（AUSCH→`scrap_rate`）。
`quantity` 以三精度字符串（如 `"1000.000"`）保留 SAP 侧格式；每行使用唯一 `child_uid`
与正确 `parent_uid`，并保留 `bom_number` / `bom_alt` / `item_category` / `item_no` /
`revision_id` / `plant` / `usage` / `scrap_rate`（部件损耗率）等扩展字段。

相对 ECC 基线预置确定性差异（供 BOM 比对演示）：

- 新增组件：`G200000020` 下新增原材料 `G300000108`（`1500.000` EA，损耗 `4.00`；
  ECC 中 `G200000020` 无子 BOM，该物料仅存在于 `G200000018` 下）。
- 数量+损耗差异（4 行）：`G200000019` `1000.000`/无 → `1200.000`/`5.00`；
  `G200000020` `2000.000`/无 → `2500.000`/`3.00`；
  `G300000115` `3000.000`/无 → `3500.000`/`2.50`；
  `G300000116` `2000.000`/`20.00` → `2100.000`/`15.00`。
- 其余 13 行组件与 ECC 完全一致（含损耗 `10.00`/`100.00`/`5.00` 的三行）。

```sh
curl https://mocktc.bjlzc.cn/tc/v1/fixtures/20260810-sap-alignment-diff-G100000013.json
curl "https://mocktc.bjlzc.cn/tc/v1/fixtures/20260810-sap-alignment-diff-G100000013.json/query?part_id=G300000108&exact=1"
curl "https://mocktc.bjlzc.cn/tc/v1/fixtures/20260810-sap-alignment-diff-G100000013.json/query?part_id=G200000019&exact=1"
```

## 本地运行


```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
MOCKTC_PORT=18120 .venv/bin/python mocktc_app/app.py
# 打开 http://127.0.0.1:18120/
```

测试：

```sh
python3 -m unittest discover -s tests -v
```

## 部署（ECC 主机 erphost）

应用部署在 ECC 主机（62GB 内存），仅使用 `/oracle` 磁盘，通过 FRP 穿透到公网
`39.104.206.210:18120`，公网 nginx 再将 `mocktc.bjlzc.cn` 反向代理到该端口。

- 运行时目录：`/oracle/mocktc`（数据在 `/oracle/mocktc/data`）
- 独立 Python 3.11：`/oracle/python311`（不修改全局 python）
- 虚拟环境：`/oracle/mocktc/venv`
- 服务端口：`127.0.0.1:18120`

fixture 数据随仓库发布（`mocktc_app/fixtures/`，deploy.sh 会安装到
`/oracle/mocktc/fixtures/`）；如需从其他目录加载可设置
`MOCKTC_FIXTURE_DIR=/oracle/mocktc/fixtures`（默认即此目录）。

一键上传并部署（在控制服务器执行）：

```sh
python3 scripts/upload_and_deploy.py
```

或在目标主机手动执行：

```sh
tar -xzf mocktc-src.tar.gz -C /oracle/mocktc
bash /oracle/mocktc/scripts/deploy.sh
```

开机自启：`deploy.sh` 会 `systemctl enable mocktc.service frpc-mocktc.service`。

公网接入步骤：

1. 部署应用与 FRP 客户端（上述脚本）
2. 在公网服务器 `39.104.206.210` 的 nginx 路由配置中追加
   `scripts/nginx-mocktc-vhost.conf` 的 server 块并 reload
3. 配置 `mocktc.bjlzc.cn` 的 DNS 指向 `39.104.206.210`
4. DNS 生效后签发证书：
   ```sh
   certbot certonly --webroot -w /opt/bjlzc-agent-public-router/acme-challenges \
     -d mocktc.bjlzc.cn --config-dir ... --renew-by-default
   ```
   并将证书安装到 `/opt/bjlzc-agent-public-router/certs/mocktc.bjlzc.cn/` 后 reload nginx

## 项目结构

```
mocktc_app/          Flask 应用（app.py + templates/ + static/）
systemd/             mocktc.service、frpc-mocktc.service

## 内网受保护构建

客户离线介质使用 `scripts/build-protected-release.sh OUTPUT_DIR` 生成本机可执行发行目录。
交付物不包含 Python 源码或源码映射；模板、静态资源和两组基准 fixture 随发行包提供，
仅在全新数据目录的首次启动复制到外部持久化目录
`/var/lib/xiaogang/mocktc/fixtures`，并写入初始化标记。SQLite 数据库、变更历史、管理员
令牌和运行日志不写入镜像；已有数据库或 fixture 状态时不再回填内置文件，因此
重启和镜像升级不会复活管理员已删除的数据。
scripts/             deploy.sh、upload_and_deploy.py、FRP/nginx 配置
tests/               unittest 接口测试
```

## 安全说明

- 默认无鉴权，仅用于内网/联调环境；如需保护可设置 `MOCKTC_API_TOKEN` 环境变量
- fixture 只读接口防目录穿越：仅接受启动时注册的合法文件名（正则
  `^[A-Za-z0-9][A-Za-z0-9._-]*$` 且不含 `..`、路径分隔符、空字节），
  查询过程不触达文件系统，非法名称返回 400、未注册名称返回 404
- FRP token 从目标主机已有配置继承，不写入 Git
- 生产系统请使用真实 Teamcenter
