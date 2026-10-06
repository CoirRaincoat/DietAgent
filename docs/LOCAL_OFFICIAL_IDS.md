# 官方1–50用户ID：显式本地接入（2026-10-04）

## 10月6日晚准确b112d98身份补证（优先于下方旧镜像）

当前交付分支`codex/competition-delivery-20261006`候选b112d98，原准确ZIP不变。`runtime/delivery_final_20261006-7d31494/official-http/summary.json`success=true：从同ZIP源码真实Windows Uvicorn/HTTP核50个ID与自身原记录、逐人完整重试、跨用户409/未知与演示ID404、非法0为422及original demo隐藏；兼容JSON/SSE身份入口覆盖1和50，默认无Key原生/兼容503。身份成功用明确披露的本地解析替身、菜单为空，不当模型／排菜质量／首Token证明。

只读源脱敏JSON、原SHA不变；详细版及官方对话未读，MD／JSON／JSONL／manifest不含健康字段。会话库PRIVATE留本地ignored runtime，不能公开或随源码包发送。当前代理Docker配置权限拒绝，**新包容器／单文件只读挂载／Compose／Nginx仍未验**，不能沿用下方f6b6dec证据。

准确新用户侧脚本`runtime/delivery_final_20261006-7d31494/run_container.ps1 -ProfilesPath <已有脱敏JSON路径>`已做VerifyOnly及身份工具本机实际检查；真正执行会新建专用镜像、锁image ID、先核整包，再同镜像单文件只读挂载与原身份检查，无provider网络／key，临时仅清本轮容器，自己的镜像／证据／私有会话库保留。准备不等于Docker通过，不运行旧官方脚本冒充新验收。0新费用／外发，不自动推送／合并／提交平台。

## 2026-10-06当前候选补验

最新实际HTTP部署补验亦通过：冻结f6b6dec后端与Linux前端、现行Compose加独立隔离覆盖，真实Nginx／本机入口读50 original档案，demo隐藏、源文件不变、无key原生／兼容503及非法请求契约保持。原件`runtime/compose_delivery_20261006/275713a48388458482647c9443b78049/`；临时服务已停止。这补齐实际部署读档，不是成功模型推荐／菜单／首Token／平台验收，官方档案仍不能放进源码包或外发。

后续用户容器挂载补验已通过：`runtime/official_profile_delivery_20261006/container-b071e552/verification/summary.json`明确in_container=true，准确既有冻结镜像只读单个JSON，50 original／1–50／重试与隔离、档案不变及无key503保持。不是外发、真实模型或菜单质量验证；进程内TestClient与实际HTTP部署区别保留。下一专属Compose／Nginx实测脚本在`runtime/compose_delivery_20261006/`，尚未执行，不把脚本准备当联通成功。

用户已提供本机数据目录，实际选择脱敏版50人JSON，未读取详细版或官方对话。当前封存f6b6dec源码默认载档路径用实际文件本地核验50个源id整数1–50与各自原记录，文件前后不变；demo列表不显示original，同用户重试完整一致、跨ID409、演示ID404。身份成功请求使用明确披露的本地意图替身，不是模型或菜单评价。

默认真实适配器不配key时，原生和兼容入口均正确503，不再是下文历史保护阶段的403；没有网络／付费调用。原件MD／JSON／JSONL在忽略的`runtime/official_profile_delivery_20261006/local-verification/`，不导出健康内容。TestClient进程内验证不是容器挂载或Nginx／Compose；用户运行准备好的单文件只读挂载脚本后再确认容器。不能把本机路径写死进公开源代码或复制档案进镜像。

主办方聊天截图简短确认按官方档案1–50编号准备，模型Key由参赛方自行提供；不是完整HTTP协议。实际提供的脱敏JSON有50条，源字段是`id`，全部为整数1–50。接口内规范化为`user_id`，严格查源ID，不按数组位置、需求相似度或900001偏移选人。

## 当前可用范围

`app.api.main:app`默认启动现在调用`load_runtime_catalog(Settings.local_profile_path)`：不配置时仍是原2000菜谱＋三合成用户；配置本地文件时用该文件的50个original用户替代合成用户。不加载官方对话，不把脱敏档案当synthetic，不自动回退。缺文件、数量/ID/结构不合法会在启动失败，避免误用另一人的档案。

档案只读，不复制进镜像或公开源码。`/health`显示50与original范围；`/demo/profiles`不列出原档案。原生传`user_id: 1`，兼容接口传`user: "1"`，会话必须属于同一ID。平台具体请求与资源要求仍未提供，这不是已确认的平台协议。

**用户现已确认“模型仅解析用户需求、原档案/菜谱/排菜/理由本地”策略。** 默认适配器允许original档案本地参与，但不把档案或派生约束/成员发送给模型；仅外发用户文本与白名单结构上下文，说明全部本地生成，不再发送facts或助手历史。本地MockTransport已能走成功推荐路径；不是已验证真实模型或平台。没有新增付费调用。详见[当前边界](USER_ONLY_MODEL_BOUNDARY.md)。

## 原生启动（仅本地验证）

以下在实际候选代码目录执行；文件路径是占位符，替换为已有档案。不要把文件复制到仓库，也不要在命令行粘贴Key。

```powershell
$env:LOCAL_PROFILE_PATH = 'C:/local-private/profiles.json'
if (-not (Test-Path -LiteralPath $env:LOCAL_PROFILE_PATH -PathType Leaf)) { throw '本地档案文件不存在' }
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

也可在本地忽略的`.env`配置`LOCAL_PROFILE_PATH`。停止后取消环境变量可恢复演示模式；不要取消original保护来假装测评成功。不同模式建议分开`SESSION_DB`，不要混用旧演示会话。

## Compose显式覆盖

现有`evaluation.demo_start`不自动启用这个覆盖文件；只设置宿主`LOCAL_PROFILE_PATH`不会将文件带进容器。采用独立覆盖时用宿主路径`LOCAL_PROFILE_HOST_PATH`，容器内固定`/local-input/profiles.json`。绑定单个已有文件、只读、禁自动创建缺失宿主路径；不用挂整个私有目录。

```powershell
$env:LOCAL_PROFILE_HOST_PATH = 'C:/local-private/profiles.json'
docker compose --env-file configs/compose.env --file docker-compose.yml --file docker-compose.local-profiles.yml config --quiet
```

上面只校验配置，不启动服务，也不证明容器能读文件。真正部署前须从冻结代码重建镜像、按现有部署文档独立提供服务端模型配置、隔离会话卷，并在10月7日干净环境验证。不要用旧缓存镜像或以本次静态校验宣称Docker/比赛API通过。

## 本轮证据与未测项

`runtime/local_official_ids_20261004/report.md`及JSON/JSONL：实际脱敏原文件只读加载，源SHA/字节前后一致；50个ID各对应自身原记录、官方对话未读。实际`create_app`入口配公开本地意图替身逐一确认50个身份，跨ID会话409，900001不可用404；这是身份工程验证，不是模型理解、菜单或健康评分。

旧`runtime/local_official_ids_20261004`证据保持原样：当时实际适配器原生/SSE均403，0上游；不能拿旧阻断报告冒充新成功路径。后续仅用户解析修复另存新报告。Compose只读挂载仅离线静态核对，未启动容器；真实模型、首token、平台、独立质量未验。官方身份报告不导出健康字段/完整档案，会话库留在忽略的本地runtime。

本接入主题没有新菜单收益；此前M01已将粥换饭及牛奶蒸蛋换蛋黄蒸肉，整体仍待人工复核。后续优先交付阻断，不堆评分器/求解器。
