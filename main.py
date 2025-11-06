import aiohttp
import asyncio
import json
import os
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@register("leetcode", "Soyo", "获取 LeetCode 每日一题与随机题目（支持分类）", "1.0.0")
class LeetCodePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.NAMESPACE = "astrbot_plugin_leetcode"
        self.scheduler = AsyncIOScheduler()
        self.data_file = f"data/{self.NAMESPACE}_data.json"
        self.lc_auto_daily_ids = []
        self.context = context
        self.logger = logging.getLogger("astrbot")

    async def initialize(self):
        """插件初始化"""
        os.makedirs("data", exist_ok=True)
        if not os.path.exists(self.data_file):
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False)

        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.lc_auto_daily_ids = data.get("lc_auto_daily_ids", [])
        except Exception:
            self.lc_auto_daily_ids = []

        if self.lc_auto_daily_ids:
            self._start_cron_if_not()
            logger.info(f"[LeetCode] 已启动每日推送任务，订阅者数量: {len(self.lc_auto_daily_ids)}")

    def _save_data(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump({"lc_auto_daily_ids": self.lc_auto_daily_ids}, f, ensure_ascii=False, indent=2)

    def _start_cron_if_not(self):
        if not self.scheduler.get_jobs():
            self.scheduler.add_job(self._send_daily_problem, "cron", hour=9, minute=0)
            self.scheduler.start()

    async def _graphql(self, query: dict):
        """发送 GraphQL 请求"""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://leetcode.cn",
            "Origin": "https://leetcode.cn",
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                async with session.post("https://leetcode.cn/graphql", json=query, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error(f"[LeetCode] GraphQL 请求失败: {resp.status} {text}")
                        return None
                    result = await resp.json()
                    if "errors" in result:
                        logger.error(f"[LeetCode] GraphQL 返回错误: {result['errors']}")
                        return None
                    return result
            except asyncio.TimeoutError:
                logger.error("[LeetCode] GraphQL 请求超时")
                return None
            except Exception as e:
                logger.error(f"[LeetCode] GraphQL 请求异常: {e}")
                return None

    async def _get_problem(self, slug: str):
        """获取题目内容"""
        query = {
            "query": """
                query questionTranslations($titleSlug: String!) {
                    question(titleSlug: $titleSlug) {
                        questionId
                        questionFrontendId
                        translatedTitle
                        translatedContent
                        difficulty
                    }
                }
            """,
            "variables": {"titleSlug": slug},
            "operationName": "questionTranslations",
        }
        return await self._graphql(query)

    async def _get_daily_problem(self):
        """获取每日一题"""
        query = {
            "query": """
                query questionOfToday {
                    todayRecord {
                        question {
                            questionId
                            frontendQuestionId: questionFrontendId
                            difficulty
                            translatedTitle
                            titleSlug
                        }
                    }
                }
            """,
            "operationName": "questionOfToday",
            "variables": {},
        }

        res = await self._graphql(query)
        if not res or "data" not in res:
            raise ValueError("LeetCode 返回空数据")

        today_record = res["data"].get("todayRecord")
        if not today_record or len(today_record) == 0:
            raise ValueError("今日没有每日一题")

        data = today_record[0]["question"]
        slug = data["titleSlug"]
        problem_data = await self._get_problem(slug)

        if not problem_data or "data" not in problem_data or not problem_data["data"].get("question"):
            raise ValueError("无法获取题目详细内容")

        question = problem_data["data"]["question"]
        content = question.get("translatedContent", "")
        if not content:
            content = "题目内容获取失败，请访问链接查看"

        # 处理 frontendQuestionId 别名
        question_id = data.get("frontendQuestionId") or data.get("questionFrontendId", "")
        
        return {
            "id": question_id,
            "title": data.get("translatedTitle", ""),
            "difficulty": data.get("difficulty", ""),
            "slug": slug,
            "url": f"https://leetcode.cn/problems/{slug}",
            "content": content,
        }

    async def _send_daily_problem(self):
        """定时推送每日一题"""
        problem = await self._get_daily_problem()
        for session_id in self.lc_auto_daily_ids:
            try:
                id_str = f"{problem['id']}. " if problem.get('id') else ""
                msg = (
                    f"## LeetCode 每日一题\n"
                    f"### {id_str}{problem['title']} ({problem['difficulty']})\n"
                    f"---\n{problem['content']}\n---\n🔗 {problem['url']}"
                )
                await self.context.send_message(session_id, MessageEventResult.plain(msg))
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"推送失败 ({session_id}): {e}")

    # -------------------- 指令部分 --------------------

    @filter.command("lcd")
    async def lcd(self, event: AstrMessageEvent):
        """获取每日一题"""
        try:
            problem = await self._get_daily_problem()
            id_str = f"{problem['id']}. " if problem.get('id') else ""
            msg = (
                f"## LeetCode 每日一题\n"
                f"### {id_str}{problem['title']} ({problem['difficulty']})\n"
                f"---\n{problem['content']}\n---\n🔗 {problem['url']}"
            )
            yield event.plain_result(msg)
        except Exception as e:
            yield event.plain_result(f"⚠️ 获取每日一题失败: {e}")

    @filter.command("lcr")
    async def lcr(self, event: AstrMessageEvent):
        """随机获取一题（支持分类：hot/all/sql/interview/75）"""
        import random

        text = (event.message_str or "").strip().lower()
        slug_map = {
            "hot": "leetcode-curated-algo-100",
            "all": "",
            "sql": "sql-50",
            "interview": "top-interview-questions",
            "75": "leetcode-75",
        }
        category = slug_map.get(text, "leetcode-curated-algo-100")

        query = {
            "query": """
                query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
                    problemsetQuestionList(categorySlug: $categorySlug, limit: $limit, skip: $skip, filters: $filters) {
                        data {
                            titleSlug
                            translatedTitle
                            difficulty
                        }
                    }
                }
            """,
            "variables": {
                "categorySlug": category if category else None,
                "limit": 100,
                "skip": 0,
                "filters": {}
            },
            "operationName": "problemsetQuestionList",
        }

        res = await self._graphql(query)
        if not res or "data" not in res or not res["data"].get("problemsetQuestionList"):
            error_msg = "⚠️ 无法获取题库列表，请稍后再试。"
            if res and "errors" in res:
                error_msg += f"\n错误信息: {res['errors']}"
            yield event.plain_result(error_msg)
            return

        problemset = res["data"]["problemsetQuestionList"]
        questions = problemset.get("questions", []) if "questions" in problemset else problemset.get("data", [])
        if not questions:
            yield event.plain_result(f"⚠️ 分类 `{text or 'hot'}` 下没有题目。")
            return

        question = random.choice(questions)
        slug = question["titleSlug"]

        # 获取详细内容
        prob_data = await self._get_problem(slug)
        if not prob_data or "data" not in prob_data or not prob_data["data"].get("question"):
            # 如果无法获取详细内容，至少返回基本信息
            msg = (
                f"## LeetCode 随机题 ({text or 'HOT 100'})\n"
                f"### {question.get('translatedTitle', '')} ({question.get('difficulty', '')})\n"
                f"---\n⚠️ 无法获取题目详细内容，请访问链接查看\n---\n"
                f"🔗 https://leetcode.cn/problems/{slug}"
            )
            yield event.plain_result(msg)
            return

        problem = prob_data["data"]["question"]
        content = problem.get("translatedContent", "")
        if not content:
            content = "题目内容获取失败，请访问链接查看"

        msg = (
            f"## LeetCode 随机题 ({text or 'HOT 100'})\n"
            f"### {question.get('translatedTitle', '')} ({question.get('difficulty', '')})\n"
            f"---\n{content}\n---\n🔗 https://leetcode.cn/problems/{slug}"
        )
        yield event.plain_result(msg)

    @filter.command("lcauto")
    async def lcauto(self, event: AstrMessageEvent):
        """切换每日推送订阅状态"""
        umo_id = event.unified_msg_origin
        if umo_id in self.lc_auto_daily_ids:
            self.lc_auto_daily_ids.remove(umo_id)
            self._save_data()
            yield event.plain_result(f"❌ 已取消 {umo_id} 的每日一题推送订阅。")
        else:
            self.lc_auto_daily_ids.append(umo_id)
            self._save_data()
            self._start_cron_if_not()
            yield event.plain_result(f"✅ 已为 {umo_id} 开启每日推送（每天 9:00）")

    async def terminate(self):
        """插件卸载/停用时调用"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("[LeetCode] 调度器已停止")
