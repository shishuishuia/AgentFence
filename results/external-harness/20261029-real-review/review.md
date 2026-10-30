我先查看任务快照和项目文件结构。# 代码审查报告（只读静态审查）

审查范围：`day1/` ~ `day6/` 共 14 个文件。当前为只读审查，未修改文件；行号以任务快照为准。整体是循序渐进的学习项目，主流程可读，主要风险集中在 **day2 分组查询、day6 配置健壮性、删除逻辑、输入规范化** 和 **重复代码**。

## 优先修复清单

| 优先级 | 位置 | 问题 |
|---|---|---|
| P1 | `day2/contact_manager_v2.py:45-51` | 按分组查询只打印第一条结果 |
| P1 | `day6/app.py:27-58` | 配置只校验 JSON 语法，不校验结构；合法 JSON 也会崩 |
| P2 | 5 处删除函数 | 边遍历边 `remove`，写法脆弱 |
| P2 | `day4/student.py:12-13` | `__str__` 写死 `"Student"`，子类打印错误 |
| P2 | `day5/main.py:13` | `from models import` 导入方式不可移植 |
| P2 | 多处 `input()` | 未统一 `.strip()`，查询/删除易失败 |
| P2 | `day1/dict.py`、`day1/list.py` | 顶层演示代码有副作用 |
| P3 | `day1/list.py:43-67` | 注释与真实运行状态不一致 |
| P3 | `day2/contact_manager_v2.py:58-66` | 三引号包裹的死代码 |
| P3 | `day3/dedup_sort.py:17,34` | O(n²) 去重、缺字段 KeyError |
| P3 | `day6/app.py:82-89` | `isdigit()` 校验不足 |
| P3 | 多处 | 重复实现、文档/版本/换行符不一致 |

---

## 一、功能性/高风险问题

### 1. `day2/contact_manager_v2.py:45-51` 按分组查询只显示第一条

```python
elif choice == "3":
    group = input("请输入要查询的分组: ")
    result = [...]
    if not result:
        print(f"通讯录中没有分组为{group}的联系人!")
    else:
        print(result[0])          # 第 51 行
```

分组查询通常命中多人，但这里只打印 `result[0]`。同函数的前缀查询（42-43 行）和 `day5/models.py:86-87` 都是遍历全部，属于复制粘贴遗漏。

**建议**：

```python
else:
    for line in result:
        print(line)
```

同类问题：`day2/contact_manager_v2.py:28-34`、`day5/models.py:54-61`、`day6/app.py:130-134` 按姓名查询也只取 `result[0]`。若允许重名，应遍历；若要求姓名唯一，应在添加时校验并提示。

---

### 2. `day6/app.py:27-58` 配置加载不校验结构，合法 JSON 也会崩溃

`load_config()` 只捕获 `JSONDecodeError`，但以下内容都能通过 `json.load`：

- `[]` → `main` 第 186 行 `config['app_name']` 抛 `TypeError`
- `{}` → 第 186 行抛 `KeyError`，第 92/94 行 `config['default_group']` 也抛 `KeyError`
- `{"max_contacts": "100"}` → 第 99 行 `len(contacts) >= "100"` 抛 `TypeError`

另外：

- 第 48、53、58 行直接 `return DEFAULT_CONFIG`，返回的是模块级可变字典；调用方一旦修改，会污染后续所有兜底配置。
- 第 55 行 `except Exception` 过宽，会吞掉本应暴露的编程错误。

**建议**：合并默认值、校验类型、返回副本；异常收窄为 `OSError`/`JSONDecodeError`/`ValueError`。

```python
def load_config(config_path=None):
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except FileNotFoundError:
        print(f"配置文件 {config_path} 不存在，使用默认配置")
        return dict(DEFAULT_CONFIG)
    except json.JSONDecodeError:
        print(f"配置文件 {config_path} 格式错误，使用默认配置")
        return dict(DEFAULT_CONFIG)
    except OSError as e:
        print(f"读取配置文件失败: {e}，使用默认配置")
        return dict(DEFAULT_CONFIG)

    if not isinstance(loaded, dict):
        print("配置根节点必须是 JSON 对象，使用默认配置")
        return dict(DEFAULT_CONFIG)

    config = {**DEFAULT_CONFIG, **loaded}

    if (not isinstance(config.get("max_contacts"), int)
            or isinstance(config["max_contacts"], bool)
            or config["max_contacts"] < 0):
        print("max_contacts 必须是非负整数，使用默认配置")
        return dict(DEFAULT_CONFIG)

    if not isinstance(config.get("default_group"), str) or not config["default_group"]:
        print("default_group 必须是非空字符串，使用默认配置")
        return dict(DEFAULT_CONFIG)

    print(f"已加载配置: {config_path}")
    return config
```

---

### 3. 删除联系人时边遍历边修改列表（5 处）

位置：

- `day1/contact_manager.py:11-15`
- `day1/test.py:176-182`
- `day2/contact_manager_v2.py:12-16`
- `day5/models.py:30-34`
- `day6/app.py:110-114`

```python
for contact in contacts:
    if contact["name"] == name:
        contacts.remove(contact)
        print(f"已删除联系人 {name}！")
        return
```

**问题**：在 `for` 迭代过程中修改列表。当前因为紧跟 `return`，不会触发 `RuntimeError`，但这是脆弱写法：一旦去掉 `return`、增加“删除后继续查找”等功能，就会跳元素或抛 `RuntimeError: list changed size during iteration`。另外 `remove` 按值删除，遇到重复字典可能删错对象。

**建议**：按索引删除。

```python
def delete_contact(contacts):
    name = input("请输入要删除的姓名: ").strip()
    for i, contact in enumerate(contacts):
        if contact["name"] == name:
            del contacts[i]
            print(f"已删除联系人 {name}！")
            return
    print(f"通讯录中没有 {name}！")
```

若要删除所有同名联系人：

```python
kept = [c for c in contacts if c["name"] != name]
if len(kept) == len(contacts):
    print(f"通讯录中没有 {name}！")
else:
    contacts[:] = kept
    print(f"已删除联系人 {name}！")
```

---

### 4. `day4/student.py:12-13`（及 `day4/student_1.py:42-47`）`__str__` 写死类名

```python
def __str__(self):
    return f"Student({self.name}, {self.student_id})"
```

`GraduateStudent` 没有重写 `__str__`，因此 `print(g1)` 输出 `Student(王五, 2024003)`，而不是 `GraduateStudent(...)`，与 `isinstance`/多态示例不一致。

**建议**：

```python
def __str__(self):
    return f"{type(self).__name__}({self.name}, {self.student_id})"
```

`day4/student_1.py:42-47` 同样修改；更推荐让 `student_1.py` 直接 `from student import Student, GraduateStudent`，避免两份类定义。

---

### 5. `day5/main.py:13` 导入方式脆弱

```python
from models import add_contact, delete_contact, search_contact, show_all
```

**问题**：

- 从项目根运行 `python -m day5.main` 时，`sys.path` 是项目根，找不到 `day5/models.py`，会 `ModuleNotFoundError`。
- `models` 名字过于通用，容易和已安装的第三方 `models` 包冲突。

**建议**：把 `day5` 变成包：

```text
day5/__init__.py
day5/main.py:  from .models import add_contact, ...
运行:          python -m day5.main
```

若想保留 `python day5/main.py` 直接运行，可加兼容：

```python
try:
    from .models import add_contact, delete_contact, search_contact, show_all
except ImportError:
    from models import add_contact, delete_contact, search_contact, show_all
```

---

## 二、健壮性/输入校验

### 6. 输入未统一 `.strip()`（多处）

位置：

- `day1/contact_manager.py:3,4,10,19`
- `day1/test.py:143,144,169,197`
- `day2/contact_manager_v2.py:3,4,5,11,29,37,46`
- `day5/models.py:17,18,19,28,53,65,78`
- `day6/app.py:109,131,137,147`（`add_contact` 在 76/83/92 行已 strip，删除/查询没有）

**问题**：用户输入 `" 张三 "` 时，添加/查询/删除结果不一致；day6 中“添加时 strip、删除/查询时不 strip”尤其明显。

**建议**：所有 `input(...)` 后加 `.strip()`，并拒绝空姓名/空手机号；菜单 `choice` 也建议 strip。

```python
name = input("请输入姓名: ").strip()
if not name:
    print("姓名不能为空")
    return
```

---

### 7. `day6/app.py:82-89` 手机号校验不足

```python
elif not phone.isdigit():
    print("手机号必须是纯数字，请重新输入！")
```

`str.isdigit()` 对全角数字 `"１３８"`、阿拉伯-印度数字也返回 `True`，并且不校验长度；`day1`/`day2`/`day5` 则完全没有手机号校验。

**建议**：按 11 位手机号校验（如允许座机/国际号码再调整正则）。

```python
import re

PHONE_RE = re.compile(r"[0-9]{11}")

...
if not PHONE_RE.fullmatch(phone):
    print("手机号必须是 11 位数字，请重新输入！")
```

---

### 8. `day6/app.py:98-101` 上限校验时机不对

用户已经输入完姓名、手机号、分组后，才提示“通讯录已达上限”，输入被浪费。

**建议**：把检查移到 `add_contact()` 开头（第 72 行前），先判断再询问：

```python
if len(contacts) >= config["max_contacts"]:
    print(f"通讯录已达上限 ({config['max_contacts']} 人)，无法继续添加！")
    return
```

---

### 9. `day3/dedup_sort.py:34` 缺少字段会 KeyError

```python
field_value = contact[key]
```

若某个联系人字典缺少 `key` 字段，整个函数崩溃。

**建议**：

```python
for i, contact in enumerate(contacts):
    if key not in contact:
        raise ValueError(f"第 {i} 条记录缺少字段 {key!r}")
    field_value = contact[key]
```

或按业务改为跳过并打印警告。

---

## 三、可维护性/结构问题

### 10. `day1/dict.py`、`day1/list.py` 顶层有副作用

这两个文件更像学习笔记，但大量演示代码在模块顶层执行，导入时会打印和修改数据：

- `day1/dict.py:30-81, 91-102, 110-132, 140-147, 155-173`
- `day1/list.py:23-72, 79-95, 110-129, 138-176, 186-200`

尤其 `day1/list.py:186-189` 会实际执行“边遍历边删除”的错误示例，结果 `[1,2,3,4,5]` 变成 `[2,3,4,5]`（跳过 2），容易让读者误以为这是正确输出。

**建议**：把演示代码放进 `if __name__ == "__main__":`，或改成注释；至少把 186-189 行的错误示例注释掉。

---

### 11. `day2/contact_manager_v2.py:58-66` 死代码

```python
"""
def show_all(contacts):
    ...
"""
```

这是三引号字符串表达式，不是注释，也不会执行；与 69-76 行的 `show_all` 重复，容易误导。

**建议**：直接删除 58-66 行。

---

### 12. 重复实现（修 bug 要改多处）

- `day1/contact_manager.py:1-73` 与 `day1/test.py:133-291` 功能重复；
- `day4/student.py:1-26` 与 `day4/student_1.py:10-87` 类定义重复；
- `day2`、`day5`、`day6` 的联系人管理逻辑高度重复。

`day4` 两份 `Student` 是不同模块中的不同类，跨模块 `isinstance` 会失效。

**建议**：每个功能保留一份实现，其他文件通过 `import` 复用；`day1/test.py` 可改为纯笔记文件或只保留测试入口。

---

### 13. `day3/dedup_sort.py:17` O(n²) 去重

```python
if item not in result:
    result.append(item)
```

每次都要线性扫描 `result`。元素可哈希时建议：

```python
return list(dict.fromkeys(items))
```

若需要支持不可哈希元素，再保留当前写法，或使用 `seen` 集合并处理 `TypeError`。

---

## 四、文档/低优先级问题

### 14. `day1/list.py:43-67` 注释与真实运行状态不一致

由于第 37-38 行已经 `pop()` 掉了“苹果”：

- 第 43 行 `fruits[0]` 实际是“草莓”，不是注释里的“苹果”；
- 第 55 行 `if "苹果" in fruits` 为 `False`；
- 第 57 行 `"西瓜" not in fruits` 也为 `False`（西瓜还在列表里）；
- 第 60 行 `fruits.index("苹果")` 若取消注释会 `ValueError`；
- 第 67 行 `fruits.count("苹果")` 返回 `0`。

**建议**：每个小节前重新初始化 `fruits`，或把这些操作改成注释示例，并同步修正 43、55-57、60、67 行的说明。

---

### 15. 其他低优先级项

- `day1/contact_manager.py:38-41`、`day2/contact_manager_v2.py:81-84`：文档字符串写“做三件事”，但只列了一件，建议补齐或改写。
- `day6/app.py:21` 默认版本是 `1.0`，`day6/config.json:3` 是 `2.0`，建议统一版本号。
- `day5/requirements.txt` 为空：若只用标准库，可删除或写 `# 仅使用标准库`。
- `day1`~`day3` 多为 CRLF，`day6` 为 LF；建议加 `.gitattributes`：`* text=auto`、`*.py text eol=lf`。
- `day1/day1.md:2-10` 的 `list.append()`/`list.insert(2,b)` 是伪代码，`b` 未定义且 `list` 覆盖内置名；建议标注“伪代码”或换成真实变量。
- `day1/test.py` 命名过于通用：若使用 `unittest discover`（默认 `test*.py`）会被当作测试模块导入；pytest 默认不收集。建议改名为 `day1_notes.py` 之类。

---

## 建议补充的测试

项目目前没有自动化测试。上述函数大多接收 `contacts` 列表，容易测试，建议至少覆盖：

1. 分组查询命中多人时是否全部输出（对应问题 1）。
2. 删除同名/重复联系人后列表内容是否正确（对应问题 3）。
3. `load_config` 面对 `[]`、`{}`、`{"max_contacts": "100"}`、缺字段时的行为（对应问题 2）。
4. `print(GraduateStudent(...))` 是否输出 `GraduateStudent(...)`（对应问题 4）。
5. 输入前后带空格时添加/删除/查询是否一致（对应问题 6）。
6. 全角数字手机号是否被拒绝（对应问题 7）。

## 建议修复顺序

1. 先修 `day2` 分组查询和 `day6` 配置加载（用户可见的功能/崩溃问题）。
2. 统一 5 处删除逻辑，改为按索引删除。
3. 统一所有 `input().strip()` 和手机号校验。
4. 修 `day4` 的 `__str__`，并合并重复类定义。
5. 整理 `day1` 笔记文件：加 `if __name__ == "__main__"`、修正过期注释、删除死代码。
6. 最后补测试、README、`.gitattributes` 和运行说明。