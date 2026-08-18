"""示例插件 - 验证插件内核的隔离/超时/契约机制"""

import time


def echo(text=""):
    return {"message": f"你说了: {text}"}


def add(a=0, b=0):
    return {"result": a + b}


def crash():
    raise RuntimeError("插件主动崩溃，验证隔离")


def hang():
    while True:
        time.sleep(1)
