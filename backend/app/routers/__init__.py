"""业务路由汇总：跨模块链接/检索回执路由必须先于通用资源路由注册。"""
from __future__ import annotations

from app.routers import cross_links, index_search, resource

ROUTERS = [cross_links, index_search, resource]
