"""四个 test_*.py 共用的离线加载器与断言收集器。

server.py 是单文件脚本，末尾会起 scheduler 和 HTTP 服务；这里把模块的 AST 执行到
第一个 `threading.Thread(...)` 语句为止，只拿到函数与全局，进程不进服务状态。
数据目录一律指向临时目录，不碰真实的 /data。

如果以后有人在文件更前面起线程，循环会提前 break，`for ... else` 会报错提醒改加载器。
"""
import ast, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def load_server(data_dir, downloads_dir):
    """跑 server.py 的模块体（截到起线程之前），返回它的命名空间。"""
    os.environ['STREAMFORGE_DATA'] = str(data_dir)
    os.environ['STREAMFORGE_DOWNLOADS'] = str(downloads_dir)
    path = Path(__file__).with_name('server.py')
    tree = ast.parse(path.read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and ast.unparse(node).startswith('threading.Thread('):
            break
        nodes.append(node)
    else:
        raise AssertionError('server.py 里找不到 threading.Thread(...)，加载器的截断点失效了')
    ns = {'__file__': str(path)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return ns


class Checks:
    """极简断言收集：跑完按失败数决定退出码，CI 直接看退出码。"""

    def __init__(self):
        self.failures = []
        self.passed = 0

    def check(self, name, condition, detail=''):
        if condition:
            self.passed += 1
        else:
            self.failures.append(name)
        print(('PASS  ' if condition else 'FAIL  ') + name + ('' if condition else '  -> ' + str(detail)))
        return bool(condition)

    def rejects(self, name, function, *arguments):
        """函数必须抛 ValueError 才算通过（用于安全边界）。"""
        try:
            function(*arguments)
        except ValueError:
            return self.check(name + ' → 拒绝', True)
        except Exception as exc:
            return self.check(name + ' → 拒绝', False, '抛了 %s，期望 ValueError' % type(exc).__name__)
        return self.check(name + ' → 拒绝', False, '竟然被接受了')

    def done(self):
        print()
        if self.failures:
            print('失败 %d 项：%s' % (len(self.failures), self.failures))
        else:
            print('全部通过（%d 项）' % self.passed)
        sys.exit(1 if self.failures else 0)
