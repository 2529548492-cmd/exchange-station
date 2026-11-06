import io
p="app.py"; s=io.open(p,encoding="utf-8").read()
old = """try:
    from canvas_design import build_canvas_page
    CANVAS_PAGE = build_canvas_page()
except Exception:          # 画布页缺失时静默回退，不影响线上
    CANVAS_PAGE = None"""
new = """try:
    from wall_design import build_wall_page
    CANVAS_PAGE = build_wall_page(STUDIO_PAGE)     # 只加一个「画布视图」开关，其余不动
except Exception:          # 增强层缺失时静默回退，不影响线上
    CANVAS_PAGE = None"""
print("app.py patched" if old in s else "MISS")
io.open(p,"w",encoding="utf-8",newline="").write(s.replace(old,new,1))
