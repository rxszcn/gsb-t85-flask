# 400 的两条路径与两个旋钮：现状实录

只描述现状，不改任何实现与用例。复现命令：

```
PYTHONPATH=src .venv/bin/python repro/trap400.py
```

`repro/trap400.py` 里 `TESTING=True` 只是让 test client 把"未被咽下"的异常直接抛出来
（等价于生产环境里交给交互调试器/500 通道），它不参与下面任何一个判据。

两条触发路径：

- `/form`：视图里 `request.form["missing_key"]`，缺键由 Werkzeug 的 `MultiDict.__getitem__`
  抛出 `werkzeug.exceptions.BadRequestKeyError`（同时是 `KeyError` 和 `BadRequest`）。
- `/json`：POST `application/json`、体为 `{bad json`，`request.get_json()` 解析失败，
  异常经 `flask.Request.on_json_loading_failed` 重造后是基类 `BadRequest`。

## 一、十行读数逐条对账

下表为本次实际输出，列含义同脚本：`RAISED` 表示异常未被 Flask 咽下、由 test client 抛出；
`RESPONSE 400` 表示被咽下并渲染成默认错误页；`has_key` 指文本里是否含 `missing_key`，
`has_decode` 指是否含 `Failed to decode`。

| 行 | 旋钮 | 路径 | 实测结果 |
|----|------|------|----------|
| 1 | DEBUG=False, TRAP=未设置 | /form | RESPONSE 400，has_key=False，has_decode=False |
| 2 | DEBUG=False, TRAP=未设置 | /json | RESPONSE 400，has_key=False，has_decode=False |
| 3 | DEBUG=True,  TRAP=未设置 | /form | RAISED BadRequestKeyError，has_key=True |
| 4 | DEBUG=True,  TRAP=未设置 | /json | RESPONSE 400，has_key=False，has_decode=True |
| 5 | DEBUG=False, TRAP=True   | /form | RAISED BadRequestKeyError，has_key=True |
| 6 | DEBUG=False, TRAP=True   | /json | RAISED BadRequest，has_decode=False |
| 7 | DEBUG=True,  TRAP=True   | /form | RAISED BadRequestKeyError，has_key=True |
| 8 | DEBUG=True,  TRAP=True   | /json | RAISED BadRequest，has_decode=True |
| 9 | DEBUG=False, TRAP=False  | /form | RESPONSE 400，has_key=False，has_decode=False |
| 10| DEBUG=False, TRAP=False  | /json | RESPONSE 400，has_key=False，has_decode=False |

四种下场全部出现，而且同一行配置下两条路可以不同：

- **行 3 vs 行 4（DEBUG 开、TRAP 未设置）：表单抛出、JSON 咽下。**
  `/form` 的 `BadRequestKeyError` 被 `Flask.trap_http_exception` 判为应 trap
  （第三态 `None` 回落到 `self.debug`，且只对 `BadRequestKeyError` 成立），
  于是 `handle_user_exception` 直接 `raise`，细节（键名）随异常一起抛出去。
  `/json` 的基类 `BadRequest` 不满足"仅 key error"的回落条件，不被 trap，
  走 `handle_http_exception` 咽下成 400；但它在诞生时（见判据三）因为 DEBUG 开着
  保留了 `Failed to decode JSON object: ...` 的描述，所以页面反而带着解码细节。
  即 DEBUG 对表单控制的是"抛不抛"，对 JSON 控制的却是"页面给不给细节"。
- **行 5 vs 行 6（DEBUG 关、TRAP=True）：都抛出，但 JSON 细节被剥干净。**
  `TRAP_BAD_REQUEST_ERRORS=True` 时 `trap_http_exception` 对**一切** `BadRequest`
  （含基类）返回 True，两条路都抛。区别在异常诞生地：表单的 `BadRequestKeyError`
  在 `handle_user_exception` 里被置 `show_exception=True`，`get_description()`
  于是拼出 `KeyError: 'missing_key'`；JSON 那条路在 DEBUG=False 时被
  `on_json_loading_failed` 用无参 `BadRequest()` 重造，只留 Werkzeug 的固定描述
  "The browser (or proxy) sent a request ..."，解析异常被挂在 `__cause__` 上，
  文本里没有 `Failed to decode`。
- **行 7 vs 行 8（DEBUG 开、TRAP=True）：都抛出，JSON 这次带细节。**
  同样是 trap 抛出，JSON 异常这次在诞生时保留了完整描述，所以行 8 `has_decode=True`。
  旋钮一样、抛出动作一样，差别只由 DEBUG 在更上游的重造环节决定。
- **行 1/2/9/10（DEBUG 关且不显式 trap）：两条路都是无细节 400。**
  TRAP 为 `None` 回落 DEBUG=False，或显式 False，判据结果一致：咽下、渲染
  Werkzeug 默认错误页。表单键名只存在于 `show_exception=True` 时，此处为 False；
  JSON 描述已在重造时被剥掉。

## 二、三处判据点名

**判据 1：HTTP 异常抛出去还是咽下 —— `Flask.trap_http_exception`**

- 定义：`src/flask/sansio/app.py:893`（同步 `Flask` 直接继承）。
- 调用点：`src/flask/app.py:890`，`Flask.handle_user_exception` 中
  `if isinstance(e, HTTPException) and not self.trap_http_exception(e): return self.handle_http_exception(...)`；
  返回 True 则跳过错误处理器，继续走到 `handler is None: raise`（`src/flask/app.py:898` 附近）原样抛出。
- 读取的配置与分支（按代码顺序）：
  1. `TRAP_HTTP_EXCEPTIONS`（默认 `False`，`src/flask/app.py:232`）：真值为一切 HTTP 异常 trap；
     这是纯布尔，无第三态。
  2. `TRAP_BAD_REQUEST_ERRORS`（默认 `None`，`src/flask/app.py:231`）：**有第三态**。
     - 为 `None` 时：仅当 `self.debug` 为真**且**异常是 `BadRequestKeyError` 才 trap
       （代码注释原文 "if unset, trap key errors in debug mode"）。
     - 为真值时：`isinstance(e, BadRequest)` 即 trap，基类 `BadRequest` 也算——
       文档承诺的范围（只说 request dicts 取键）与这里的实际类型范围不一致，见第三节。
     - 为假值 `False` 时：不 trap。

**判据 2：表单错误页/异常给不给键名细节 —— `BadRequestKeyError.show_exception`**

- 开关位置：`src/flask/app.py:885-887`，`handle_user_exception` 开头：
  `if isinstance(e, BadRequestKeyError) and (self.debug or self.config["TRAP_BAD_REQUEST_ERRORS"]): e.show_exception = True`。
- 读的是 `self.debug`（即 `DEBUG` 配置）或 `TRAP_BAD_REQUEST_ERRORS` 的真值，二者取或；
  注意这里读 TRAP 不区分第三态，`None` 在 Python 里为假。
- 细节的真正出口在 Werkzeug：`BadRequestKeyError.description` 属性
  （werkzeug `exceptions.py:222-227`），`show_exception` 为真时才在描述后追加
  `KeyError: <键名>`；默认错误页始终渲染 `description`，没有第二道 DEBUG 闸门。
- 这解释了行 5/7：TRAP=True 导致异常被抛出，但即使不抛（例如判据 1 形态变化），
  只要这一行执行过，400 页面也会带键名。抛不抛与给不给细节在表单路上是两个独立赋值，
  只是恰好被同一组旋钮的不同读法同时触发。

**判据 3：JSON 路自己看了什么 —— `flask.Request.on_json_loading_failed`**

- 定义：`src/flask/wrappers.py:212-219`。它包住 Werkzeug 基类实现
  （`werkzeug/wrappers/request.py:631-647`，解析失败时抛
  `BadRequest(f"Failed to decode JSON object: {e}")`）：
  ```python
  def on_json_loading_failed(self, e):
      try:
          return super().on_json_loading_failed(e)
      except BadRequest as ebr:
          if current_app and current_app.debug:
              raise
          raise BadRequest() from ebr
  ```
- 这里**只看 `current_app.debug` 一个旋钮，完全不读 `TRAP_BAD_REQUEST_ERRORS`**：
  - DEBUG=True：原样重抛带 `Failed to decode ...` 描述的异常（行 4 咽下后页面带细节、行 8 抛出后异常带细节）。
  - DEBUG=False：丢掉原描述，`raise BadRequest() from ebr` 重造一个只有固定描述的
    基类 `BadRequest`，解析错误只留在异常链 `__cause__` 里（行 2/6/10 无细节）。
- 重造发生在视图执行**之中**，早于判据 1/2 所在的 `handle_user_exception`；
  等异常流到判据 1 时，它已经是基类 `BadRequest`，因此第三态回落（只认
  `BadRequestKeyError`）永远罩不到 JSON 路。这就是两条路径互相打脸的机械原因：
  JSON 的细节闸门在异常诞生地、且只认 DEBUG；表单的细节闸门在异常处理地、认 DEBUG 或 TRAP；
  而抛/咽闸门对两种异常类型还区别对待。

## 三、文档承诺与实测对不上的地方

依据 `docs/config.rst:104-113` 对 `TRAP_BAD_REQUEST_ERRORS` 的原文承诺：

> Trying to access a key that doesn't exist from request dicts like ``args``
> and ``form`` will return a 400 Bad Request error page. Enable this to treat
> the error as an unhandled exception instead ... If unset, it is enabled in
> debug mode.

逐条对账：

1. **承诺只管"request dicts 取键"，实测它还管 JSON 解码失败 —— 对不上（行 6、行 8）。**
   文档把适用对象限定为 args/form 取键产生的 key error；但 `trap_http_exception`
   在 TRAP 真值时判定的是 `isinstance(e, BadRequest)`，JSON 路重造出的基类
   `BadRequest` 同样被 trap。`tests/test_basic.py:1082-1086` 的
   `test_trap_bad_request_key_error`（`(False, True, False, False)` 这一参数组）
   甚至用 `flask.abort(400)` 断言"一切 400 都被抛"，等于测试在给超范围行为背书，
   与文档字面相反。判断：文档与实现矛盾，实现与该测试一致。
2. **"If unset, it is enabled in debug mode" 的回落只对 key error 成立 —— 文档过度泛化（行 3 对、行 4 打脸）。**
   第三态回落代码显式要求 `isinstance(e, BadRequestKeyError)`。行 3 符合承诺
   （DEBUG 开、TRAP 未设置、表单缺键 → 抛出）；行 4 同样 DEBUG 开、TRAP 未设置，
   JSON 坏体却只回 400、没有被当作未处理异常。文档没有声明这个类型例外。
3. **DEBUG 的文档承诺里完全没有"400 错误页内容随 DEBUG 变化"这一条 —— 承诺缺失（行 4）。**
   `docs/config.rst:68-78` 对 `DEBUG` 只承诺交互调试器与自动重载；实测 DEBUG 还
   决定 JSON 400 页面是否泄露 `Failed to decode JSON object: ...`（行 2 与行 4 对比，
   以及 `tests/test_json.py:14-26` 的 `test_bad_request_debug_message` 把这一行为
   钉成 `contains == debug`）。这是一个未写进 config 文档的副作用，而且与表单路
   不对称——表单缺键在 DEBUG 下根本不出页面（行 3 直接抛），JSON 在 DEBUG 下却
   出一张带内部解码细节的页面。
4. **两处内联文档同样失真。**
   - `src/flask/sansio/app.py:892-897` 的 docstring 说 "return False for all
     exceptions except for a bad request key error if TRAP_BAD_REQUEST_ERRORS is
     set to True"，但真值分支 trap 的是一切 `BadRequest`（行 6 可证）；同段
     "Bad request errors are not trapped by default in debug mode" 只对非
     key-error 成立，对 `BadRequestKeyError` 恰好相反（行 3）。
   - `CHANGES.rst:920-922`（Flask 1.0 条目）"TRAP_BAD_REQUEST_ERRORS is enabled
     by default in debug mode. BadRequestKeyError has a message with the bad key
     in debug mode"：第二句与行 3 一致，第一句若按字面理解为"所有 bad request"
     则被行 4 否定；代码注释 "if unset, trap key errors in debug mode"
     （`src/flask/sansio/app.py:912`）才是精确表述。
5. **表单路本身符合承诺。** 行 1/3/5/9 与文档对 args/form 取键的描述逐行吻合，
   也与 `tests/test_basic.py:1054-1080` 的三组参数互相对上。打脸只发生在 JSON 路
   与跨路承诺上。

## 四、收口：改动落在哪一层、谁翻、选哪边

**自洽的目标语义应当是两条正交的轴，对 /form 与 /json 同型生效：**

- 抛/咽轴：`TRAP_HTTP_EXCEPTIONS` 管一切 HTTP 异常；`TRAP_BAD_REQUEST_ERRORS`
  管一切框架隐式产生的 400（取键、JSON 解码、后续同类入口），保留 `None` 第三态
  回落 DEBUG。类型不应再决定语义，`BadRequestKeyError` 不再享受单独分支。
- 细节轴：抛出去时细节随异常走（`get_description()` 不剥）；咽下成 400 页面时，
  默认只给固定描述，"页面上是否带内部细节"不再让 DEBUG 隐式决定——要看细节就把
  异常 trap 出来看。DEBUG 回归"未处理异常交给调试器"这一个承诺。

**改动只有两个落点，都在框架层，视图与错误处理器不用动：**

1. `src/flask/sansio/app.py` 的 `trap_http_exception`：把第三态分支的
   `isinstance(e, BadRequestKeyError)` 放宽为 `isinstance(e, BadRequest)`，
   让判据 1 对两条路用同一把尺。
2. `src/flask/wrappers.py:212-219` 的 `on_json_loading_failed`：删掉
   `current_app.debug` 私设的闸门，无条件保留 Werkzeug 的原始描述重抛
   （即函数体只剩 `return super().on_json_loading_failed(e)`）。剥不剥细节改由
   咽下侧的错误响应统一决定；若坚持"咽下页不能泄露解码细节"，则需要一个能区分
   "框架隐式 400"与"用户 `abort(400, 描述)`"的标记（在重造处打标，渲染侧按标剥
   `description`），不能像现在这样在诞生地直接抹掉，因为那同时抹掉了抛出时的调试
   信息（行 6）。

**会翻的现成测试（按上面落点逐一核对）：**

- `tests/test_basic.py:1054` `test_trap_bad_request_key_error` 的
  `(True, None, False, True)` 参数组：现在钉着 DEBUG 开 + TRAP 未设置时
  `/abort`（显式 abort(400)）仍回 400。放宽第三态后，隐式/显式 `BadRequest` 都会
  被 trap，该组 `expect_abort=True` 翻成 `pytest.raises(BadRequest)`。这是唯一必翻
  的用例。注意同测试的 `/key` 三个键错误参数组与新语义全部仍然成立，不用动。
- `tests/test_json.py:14` `test_bad_request_debug_message`：TRAP=False 显式压死
  抛/咽轴，DEBUG 只影响细节。落点 2 若保留"咽下即剥细节"的渲染侧策略，此测试
  原样通过；若选"DEBUG 不再决定页面细节、一律给原始描述"，则 `contains == debug`
  断言要改成恒 True（或恒 False，取决于渲染侧策略），31 条里只有这一条受影响。
- `tests/test_json.py:28` `test_json_bad_requests` 与 `tests/test_basic.py:1090`
  `test_trapping_of_all_http_exceptions`：分别只断言 400 状态码与全量 trap，
  两种落地下都不翻。

**选择与代价：** 我选落点 1 + 落点 2 的"抛不抛统一、细节随抛出走"方案，即
TRAP（含第三态回落 DEBUG）成为唯一的抛/咽开关，DEBUG 不再在 JSON 诞生地私下剥/留
细节。代价由维护者承担：需要改 `tests/test_basic.py:1054` 那一组参数的预期
（显式 abort 在 DEBUG 下也会被 trap），并在文档里把 `TRAP_BAD_REQUEST_ERRORS`
的措辞从"request dicts 的 key error"改成"框架隐式 400"；用户侧的可见行为变化是
DEBUG 下 `abort(400)` 不再安静地回页——但这本就是 `TRAP_HTTP_EXCEPTIONS` 文档
已经描述过的调试器语义，属于纠偏而非新增惊奇。反过来若只改文档去追认现状
（把"JSON 解码在 DEBUG 下页面泄露细节、TRAP=True 时 JSON 抛无细节异常"写进
config.rst），代码零改动、零测试翻账，但两个旋钮继续在两层各读各的配置，语义
不自洽的债原样留下，所以不取。

## 附：验证

改动前后各执行一次：

```
PYTHONPATH=src .venv/bin/python -m pytest tests/test_json.py -q
```

两次均为 `31 passed`，通过数一致；本次交付只新增本文档，未改实现与任何用例。
