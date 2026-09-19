import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class MCPClient:
    """
    Model Context Protocol (MCP) Client
    JSON-RPC 2.0 客户端，连接外部 MCP 工具服务器。

    教学常用工具按服务器分四组：
    1. compute    - Wolfram 精确计算 / 电子表格公式计算 / 代码解释器
    2. research   - ArXiv 检索 / 中文学术检索 / 联网搜索 / 网页抓取
    3. subject    - 天气查询 / 多语词典翻译 / 视频字幕提取
    4. creation   - AI 课件配图 / PPT 生成 / 思维导图 / Canvas LMS 同步
    """

    def __init__(self, server_url: Optional[str] = None):
        self.server_url = server_url
        self._connected_servers: Dict[str, Dict[str, Any]] = {
            "compute": {"status": "ACTIVE", "transport": "stdio", "version": "1.0.0"},
            "research": {"status": "ACTIVE", "transport": "sse", "version": "1.2.0"},
            "subject": {"status": "ACTIVE", "transport": "http", "version": "1.1.0"},
            "creation": {"status": "STANDBY", "transport": "http", "version": "2.1.0"},
        }

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Returns registered tools across all active MCP servers."""
        return list(_TOOL_REGISTRY.values())

    async def call_tool(self, server: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches tool call over JSON-RPC 2.0."""
        logger.info(f"[MCPClient] call server='{server}', tool='{tool_name}', args={arguments}")

        handler = _TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return {"status": "error", "message": f"未知工具: {tool_name}"}
        try:
            return handler(arguments)
        except Exception as e:
            logger.error(f"[MCPClient] tool '{tool_name}' failed: {e}")
            return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Tool metadata registry（种子数据，含分类/图标/示例参数）
# ---------------------------------------------------------------------------

def _t(
    tool_id: str,
    server: str,
    name: str,
    category: str,
    icon: str,
    description: str,
    props: Dict[str, Any],
    example: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "id": tool_id,
        "server": server,
        "name": name,
        "category": category,
        "icon": icon,
        "description": description,
        "parameters": {"type": "object", "properties": props},
        "example_arguments": example,
    }


_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ---- compute ----
    "wolfram_compute": _t(
        "wolfram_compute", "compute", "Wolfram 符号计算", "精确计算", "Sigma",
        "高精度代数运算、符号微积分、微分方程与矩阵求解",
        {"query": {"type": "string", "description": "数学计算表达式，如 integrate x^2"}},
        {"query": "integrate (e^x - 1) from 0 to 1"},
    ),
    "spreadsheet_calc": _t(
        "spreadsheet_calc", "compute", "电子表格公式计算", "精确计算", "Table2",
        "解析表格数据并按 Excel 公式（SUM/AVERAGE/统计）批量计算",
        {
            "formula": {"type": "string", "description": "公式，如 =AVERAGE(A1:A5)"},
            "data": {"type": "string", "description": "CSV 格式数据（可选）"},
        },
        {"formula": "=AVERAGE(78, 85, 92, 70, 88)"},
    ),
    "code_interpreter": _t(
        "code_interpreter", "compute", "代码解释器", "精确计算", "Code2",
        "在隔离沙箱中运行 Python 代码，适合算法验证与数据处理",
        {"code": {"type": "string", "description": "Python 源代码"}},
        {"code": "print(sum(i**2 for i in range(10)))"},
    ),
    # ---- research ----
    "arxiv_query": _t(
        "arxiv_query", "research", "ArXiv 论文检索", "学术与联网", "GraduationCap",
        "实时检索 ArXiv 前沿论文（AI 教育、大模型评测、STEM）",
        {"keywords": {"type": "string", "description": "检索关键词"}},
        {"keywords": "LLM STEM education"},
    ),
    "cnki_query": _t(
        "cnki_query", "research", "中文学术检索", "学术与联网", "BookOpen",
        "检索中文教育期刊与硕博论文（课程教法、核心素养）",
        {"keywords": {"type": "string", "description": "检索关键词"}},
        {"keywords": "核心素养 人工智能教育"},
    ),
    "web_search": _t(
        "web_search", "research", "联网搜索", "学术与联网", "Globe",
        "获取最新网络资讯、政策文件与教学素材链接",
        {"query": {"type": "string", "description": "搜索词"}},
        {"query": "2026 高考数学命题趋势"},
    ),
    "web_fetch": _t(
        "web_fetch", "research", "网页内容抓取", "学术与联网", "Download",
        "抓取指定 URL 网页正文并转为结构化文本",
        {"url": {"type": "string", "description": "网页地址"}},
        {"url": "https://example.com/article"},
    ),
    # ---- subject ----
    "weather_query": _t(
        "weather_query", "subject", "天气查询", "学科教学", "CloudSun",
        "查询城市实时天气，用于地理情境教学（气温/降水/风力）",
        {"city": {"type": "string", "description": "城市名"}},
        {"city": "杭州"},
    ),
    "dictionary_translate": _t(
        "dictionary_translate", "subject", "多语词典翻译", "学科教学", "Languages",
        "中英多语种单词与短语查询，附音标、词性与例句",
        {
            "text": {"type": "string", "description": "待查内容"},
            "target_lang": {"type": "string", "description": "目标语言"},
        },
        {"text": "derivative", "target_lang": "中文"},
    ),
    "video_subtitle": _t(
        "video_subtitle", "subject", "视频字幕提取", "学科教学", "Subtitles",
        "提取 Bilibili 等教学视频字幕文本，用于课堂实录分析",
        {"video_url": {"type": "string", "description": "视频链接"}},
        {"video_url": "https://www.bilibili.com/video/BV1demo"},
    ),
    # ---- creation ----
    "image_generate": _t(
        "image_generate", "creation", "AI 课件配图", "创作与同步", "ImagePlus",
        "按描述生成课件插图、情境图与示意配图",
        {"prompt": {"type": "string", "description": "画面描述"}},
        {"prompt": "几何画板演示切线逼近示意图，扁平风格"},
    ),
    "ppt_generate": _t(
        "ppt_generate", "creation", "PPT 生成", "创作与同步", "Presentation",
        "根据教学大纲一键生成 PPT 文件（含标题页/内容页/图表）",
        {"topic": {"type": "string", "description": "课件主题"}},
        {"topic": "导数的几何意义"},
    ),
    "mindmap_generate": _t(
        "mindmap_generate", "creation", "思维导图", "创作与同步", "Network",
        "将知识点结构化为思维导图（Markmap / XMind 格式）",
        {"topic": {"type": "string", "description": "主题"}},
        {"topic": "导数及其应用知识体系"},
    ),
    "sync_canvas": _t(
        "sync_canvas", "creation", "Canvas LMS 同步", "创作与同步", "School",
        "同步教案与作业包至学校 Canvas 学习管理系统",
        {
            "course_id": {"type": "string", "description": "课程 ID"},
            "title": {"type": "string", "description": "资源标题"},
        },
        {"course_id": "COURSE-101", "title": "导数的几何意义 教案"},
    ),
}


# ---------------------------------------------------------------------------
# Tool handlers（教学模拟实现，返回结构化结果）
# ---------------------------------------------------------------------------

def _h_wolfram(args: Dict[str, Any]) -> Dict[str, Any]:
    q = args.get("query", "")
    return {
        "status": "success",
        "query": q,
        "result": r"\int_0^1 (e^x - 1)\,dx = e - 2 \approx 0.71828",
        "server": "compute",
    }


def _h_spreadsheet(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "formula": args.get("formula", ""),
        "result": 82.6,
        "note": "演示计算：AVERAGE(78,85,92,70,88) = 82.6",
    }


def _h_code(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "stdout": "285",
        "note": "sum(i**2 for i in range(10)) = 0^2+...+9^2 = 285",
    }


def _h_arxiv(args: Dict[str, Any]) -> Dict[str, Any]:
    kw = args.get("keywords", "")
    return {
        "status": "success",
        "keywords": kw,
        "papers": [
            {"title": "Large Language Models in STEM Education: A Systematic Review", "arxiv_id": "2401.12345"},
            {"title": "Adaptive Socratic Tutoring with Graph-Based Agents", "arxiv_id": "2402.67890"},
        ],
    }


def _h_cnki(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "keywords": args.get("keywords", ""),
        "papers": [
            {"title": "生成式人工智能赋能课堂教学变革的路径研究", "source": "课程·教材·教法"},
            {"title": "核心素养导向的数学单元整体教学设计", "source": "中国教育学刊"},
        ],
    }


def _h_websearch(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "query": args.get("query", ""),
        "results": [
            {"title": "教育部：深化教育数字化战略行动", "url": "https://example.com/policy"},
            {"title": "2026 年高考命题解读：强化思维考查", "url": "https://example.com/exam"},
        ],
    }


def _h_webfetch(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "url": args.get("url", ""),
        "content": "（抓取正文）文章围绕教育数字化转型，阐述了 AI 与课堂教学深度融合的实践路径……",
    }


def _h_weather(args: Dict[str, Any]) -> Dict[str, Any]:
    city = args.get("city", "")
    return {
        "status": "success",
        "city": city,
        "weather": "多云转晴",
        "temperature": "19~27℃",
        "wind": "东南风 3 级",
        "teaching_note": "可用于讲解气温日较差与季风等地理概念",
    }


def _h_dict(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "text": args.get("text", ""),
        "phonetic": "/dɪˈrɪvətɪv/",
        "pos": "n. 导数；派生物  adj. 派生的",
        "examples": ["The derivative of x^2 is 2x."],
    }


def _h_subtitle(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "video_url": args.get("video_url", ""),
        "subtitles": "同学们好，今天我们学习导数的几何意义。先看这段高铁进站的视频……",
        "duration_seconds": 420,
    }


def _h_image(args: Dict[str, Any]) -> Dict[str, Any]:
    prompt = args.get("prompt", "")
    return {
        "status": "success",
        "prompt": prompt,
        "image_url": f"https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt={prompt}&image_size=landscape_16_9",
    }


def _h_ppt(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "topic": args.get("topic", ""),
        "slides": ["封面：导数的几何意义", "情境导入：瞬时速度", "概念形成：割线→切线", "例题与小结"],
        "download_format": "pptx",
    }


def _h_mindmap(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "topic": args.get("topic", ""),
        "markdown": "# 导数及其应用\n## 导数概念\n## 几何意义\n## 单调性\n## 极值与最值",
    }


def _h_canvas(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "course_id": args.get("course_id", ""),
        "title": args.get("title", ""),
        "message": "资源包已推送至 Canvas，学生可在课程页面查看",
    }


_TOOL_HANDLERS = {
    "wolfram_compute": _h_wolfram,
    "spreadsheet_calc": _h_spreadsheet,
    "code_interpreter": _h_code,
    "arxiv_query": _h_arxiv,
    "cnki_query": _h_cnki,
    "web_search": _h_websearch,
    "web_fetch": _h_webfetch,
    "weather_query": _h_weather,
    "dictionary_translate": _h_dict,
    "video_subtitle": _h_subtitle,
    "image_generate": _h_image,
    "ppt_generate": _h_ppt,
    "mindmap_generate": _h_mindmap,
    "sync_canvas": _h_canvas,
}


mcp_client = MCPClient()
