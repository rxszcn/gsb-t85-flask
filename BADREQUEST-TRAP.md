# BADREQUEST-TRAP：DEBUG 与 TRAP_BAD_REQUEST_ERRORS 的现状盘点

不改一行代码，只把 `repro/trap400.py` 的实测读数、源码里的三处判据、文档与实测的出入、以及收口方案写清楚。

复现命令：`PYTHONPATH=src .venv/bin/python repro/trap400.py`
（app 均设 `TESTING=True`，所以被 trap 的异常会经 `handle_exception` 的 `PROPAGATE_EXCEPTIONS`（默认 `None` → 取 `testing or debug`，`src/flask/app.py:930`）一路抛到 test client。）

## 一、十行实测读数

`has_key` = 响应/异常描述里含 `missing_key`；`has_decode` = 含 `Failed to decode`。

| # | 组合 | 端点 | 结果 | has_key | has_decode |
|---|------|------|------|---------|------------|
| 1 | debug=F, trap=unset | /form | RESPONSE 400 | False | False |
| 2 | debug=F, trap=unset | /json | RESPONSE 400 | False | False |
| 3 | debug=T, trap=unset | /form | RAISED BadRequestKeyError | True | False |
| 4 | debug=T, trap=unset | /json | RESPONSE 400 | False | True |
| 5 | debug=F, trap=True | /form | RAISED BadRequestKeyError | True | False |
| 6 | debug=F, trap=True | /json | RAISED BadRequest | False | False |
| 7 | debug=T, trap=True | /form | RAISED BadRequestKeyError | True | False |
| 8 | debug=T, trap=True | /json | RAISED BadRequest | False | True |
| 9 | debug=F, trap=False | /form | RESPONSE 400 | False | False |
| 10 | debug=F, trap=False | /json | RESPONSE 400 | False | False |

### /form：为什么 DEBUG 开着反而抛出去、关着只回 400

`request.form["missing_key"]` 抛 `BadRequestKeyError`。`TRAP_BAD_REQUEST_ERRORS` 是三态，默认 `None`（`src/flask/app.py:231`），而 `trap_http_exception` 对 `None` 的兜底规则是"未设置时，debug 模式下 trap key error"（`src/flask/sansio/app.py:916-921`）。所以：

- 第 3 行（debug=T, trap=unset）：`None + debug` 命中兜底 → trap → 异常被重新抛出，经 `PROPAGATE_EXCEPTIONS`（TESTING=True → propagate）抛到 client，即 RAISED。
- 第 1 行（debug=F, trap=unset）：兜底不命中，`None` 又是 falsy → 不 trap → `handle_http_exception` 正常渲染 400 页面。

### /json：TRAP 开着抛了却没细节，DEBUG 开着反而回 400 还带解码失败

这是两个判据叠加的结果，不是 TRAP 剥的细节：

- 细节在更早的 `Request.on_json_loading_failed`（`src/flask/wrappers.py:212`）就被决定了：它只看 `current_app.debug`，debug 关着就 `raise BadRequest() from ebr`，抛一个全新的、描述被重置为通用文案的 `BadRequest`，`Failed to decode JSON object: ...` 到此为止。
- 第 6 行（debug=F, trap=True）：细节已被剥掉，然后 `trap_http_exception` 里 `trap_bad_request=True` → `isinstance(e, BadRequest)` 命中 → trap → 抛到 client。所以 RAISED 但 `has_decode=False`。
- 第 4 行（debug=T, trap=unset）：`on_json_loading_failed` 见 debug 开着，原样抛出带解码信息的 `BadRequest`；但兜底规则只认 `BadRequestKeyError`，普通 `BadRequest` 不被 trap → 走 `handle_http_exception` 回 400，页面上带着 `Failed to decode`。

## 二、三处判据

1. **抛出还是咽下**：`Flask.trap_http_exception`（`src/flask/sansio/app.py:893`），在 `handle_user_exception`（`src/flask/app.py:890`）里被调用。读 `TRAP_HTTP_EXCEPTIONS`（总开关）和 `TRAP_BAD_REQUEST_ERRORS`。后者**有第三态**：`None`（默认）= 仅 debug 下 trap `BadRequestKeyError`；`True` = trap 一切 `BadRequest` 子类（含 JSON 解码失败、`abort(400)`）；`False` = 全不 trap。trap 则重新 raise，不 trap 则交给 `handle_http_exception` 渲染错误页。
2. **错误页给不给细节**：`handle_user_exception` 开头（`src/flask/app.py:885-888`），当 `self.debug or self.config["TRAP_BAD_REQUEST_ERRORS"]` 为真时给 `BadRequestKeyError` 置 `e.show_exception = True`；werkzeug 的 `BadRequestKeyError.description` 按此 flag 决定是否在 400 页面里拼上 `KeyError: 'missing_key'`。注意这里 `TRAP_BAD_REQUEST_ERRORS` 是按真值读的，`None`/`False` 都算关，没有第三态语义。
3. **JSON 这条路自己看的**：`Request.on_json_loading_failed`（`src/flask/wrappers.py:212-219`），只看 `current_app.debug` 一个两态开关，**完全不看** `TRAP_BAD_REQUEST_ERRORS`。debug 开 → 原样抛出带 `Failed to decode` 的 `BadRequest`；debug 关 → 抛一个描述被重置的全新 `BadRequest()`，细节就地销毁，下游谁也救不回来。

## 三、文档承诺 vs 实测

`docs/config.rst:104-112` 对 `TRAP_BAD_REQUEST_ERRORS` 的承诺是："Trying to access a key that doesn't exist from request dicts like ``args`` and ``form`` will return a 400 ... Enable this to treat the error as an unhandled exception instead ... If unset, it is enabled in debug mode."

对不上的地方：

- **承诺只管"缺键"，实测管一切 400**。文档把作用域限定为 request dict 缺键，但实现是 `isinstance(e, BadRequest)`（`src/flask/sansio/app.py:924`）。实测第 6 行：一段坏 JSON 的解码失败也被当成 unhandled 抛出——这跟"缺键"毫无关系。同一条规则还把 `abort(400)` 也 trap 了（`tests/test_basic.py:1058` 的 `expect_abort=False` 分支钉死了这个行为）。
- **"If unset, it is enabled in debug mode" 只对 key error 成立，文档没划线**。实测第 3 行（/form 抛出）符合承诺，但第 4 行同为 debug=T、trap=unset，/json 的普通 `BadRequest` 并不 trap，照样回 400。文档读不出"enabled in debug mode"其实仅限 `BadRequestKeyError`。
- **DEBUG 与细节的关系文档只字未提**。`docs/config.rst:68-78` 的 `DEBUG` 只承诺 debugger 和 reloader，没承诺"400 页面是否带 `missing_key` / `Failed to decode` 由 DEBUG 决定"，但实测第 3、4 行和 `tests/test_json.py:14` 的 `test_bad_request_debug_message`（`contains == debug`）都钉死了这个行为。

## 四、收口：改哪一层、翻哪条测试、谁承担代价

两个旋钮语义自洽的最小落点：**`trap_http_exception`（`src/flask/sansio/app.py:893`）**，把 `trap_bad_request` 分支从 `isinstance(e, BadRequest)` 收窄为 `isinstance(e, BadRequestKeyError)`。

选这边的理由：文档承诺的作用域本来就是"request dict 缺键"，收窄是让实现回归文档，而不是改文档迁就实现。收窄后语义干净地一分为二——`TRAP_BAD_REQUEST_ERRORS` 只管"缺键异常抛不抛"，`DEBUG` 只管"错误页/异常带不带细节"，JSON 解码失败不再被 TRAP 误伤（第 6 行会从 RAISED 变回 RESPONSE 400），`abort(400)` 也不再被牵连。

代价与承担者：

- 会翻的测试是 `tests/test_basic.py:1058` 的 `test_trap_bad_request_key_error`：参数组 `(False, True, False, False)` 里 `expect_abort=False` 钉住了"trap=True 时 `abort(400)` 也抛出"，收窄后 `abort(400)` 回落为正常 400 响应，这条断言必须改。
- `tests/test_json.py:14` 的 `test_bad_request_debug_message` 不受影响（它显式设 `TRAP_BAD_REQUEST_ERRORS=False`，且只验证细节跟 DEBUG 走，收窄后依然成立）。
- 代价由"依赖 TRAP 拦截非缺键类 400（含 `abort(400)`、JSON 解码失败）的下游用户"承担——他们需要的其实是 `TRAP_HTTP_EXCEPTIONS` 或自定义 error handler，这次改动把这条被文档从未承诺过的隐式路径摆到明面上。

（反面方案是改 `on_json_loading_failed` 让它也读 TRAP，那会直接翻 `test_bad_request_debug_message` 的 `contains == debug`，且等于承认"TRAP 管所有 400"的现状再往上叠细节语义，两个旋钮缠得更死，不取。）
