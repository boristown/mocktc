# Mock Teamcenter（mocktc）

轻量级 **Siemens Teamcenter 模拟系统**，仅用于接口联调测试。为外部系统（如 ECC）
提供 Teamcenter 风格的 RESTful BOM 接口，内置简单界面、接口日志查看器和示例 BOM 数据。
部署地址：`https://mocktc.bjlzc.cn`（DNS 配置后生效）。

> 这不是真实的 Siemens Teamcenter，不包含任何西门子授权组件，仅模拟常见接口形态。

## 功能

- RESTful 接口：物料（Items）、版本（Revisions）、BOM 结构（单层/多级展开）
- 内置示例 BOM 数据（3 个总成、8 个 BOM 头、24 行 BOM 行、21 个物料）
- 接口日志：自动记录每次 `/tc/v1/*` 调用的请求/响应、耗时、状态码，界面实时查看
- 简单界面：首页 / API 文档 / 接口日志 / 数据浏览
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
scripts/             deploy.sh、upload_and_deploy.py、FRP/nginx 配置
tests/               unittest 接口测试
```

## 安全说明

- 默认无鉴权，仅用于内网/联调环境；如需保护可设置 `MOCKTC_API_TOKEN` 环境变量
- FRP token 从目标主机已有配置继承，不写入 Git
- 生产系统请使用真实 Teamcenter
