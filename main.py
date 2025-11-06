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
        async with aiohttp.ClientSession() as session:
            async with session.post("https://leetcode.cn/graphql", json=query) as resp:
                return await resp.json()

    async def _get_problem(self, slug: str):
        """获取题目内容"""
        query = {
            "query": """
                query questionTranslations($titleSlug: String!) {
                    question(titleSlug: $titleSlug) {
                        translatedTitle
                        translatedContent
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
        data = (await self._graphql(query))["data"]["todayRecord"][0]["question"]
        slug = data["titleSlug"]
        problem_data = await self._get_problem(slug)
        return {
            "id": data["frontendQuestionId"],
            "title": data["translatedTitle"],
            "difficulty": data["difficulty"],
            "slug": slug,
            "url": f"https://leetcode.cn/problems/{slug}",
            "content": problem_data["data"]["question"]["translatedContent"],
        }

    async def _send_daily_problem(self):
        """定时推送每日一题"""
        problem = await self._get_daily_problem()
        for session_id in self.lc_auto_daily_ids:
            try:
                msg = (
                    f"## LeetCode 每日一题\n"
                    f"### {problem['id']}. {problem['title']} ({problem['difficulty']})\n"
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
        problem = await self._get_daily_problem()
        msg = (
            f"## LeetCode 每日一题\n"
            f"### {problem['id']}. {problem['title']} ({problem['difficulty']})\n"
            f"---\n{problem['content']}\n---\n🔗 {problem['url']}"
        )
        yield event.plain_result(msg)

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
                        questions {
                            titleSlug
                            translatedTitle
                            difficulty
                        }
                    }
                }
            """,
            "variables": {"categorySlug": category, "limit": 100, "skip": 0, "filters": {}},
            "operationName": "problemsetQuestionList",
        }
    
        res = await self._graphql(query)
        if not res or "data" not in res or not res["data"].get("problemsetQuestionList"):
            yield event.plain_result("⚠️ 无法获取题库列表，请稍后再试。")
            return
    
        questions = res["data"]["problemsetQuestionList"]["questions"]
        if not questions:
            yield event.plain_result(f"⚠️ 分类 `{text or 'hot'}` 下没有题目。")
            return
    
        question = random.choice(questions)
        slug = question["titleSlug"]
    
        # 获取详细内容
        prob_data = await self._get_problem(slug)
        if not prob_data or "data" not in prob_data or not prob_data["data"].get("question"):
            yield event.plain_result("⚠️ 无法获取题目详细信息。")
            return
    
        problem = prob_data["data"]["question"]
        msg = (
            f"## LeetCode 随机题 ({text or 'HOT 100'})\n"
            f"### {question['translatedTitle']} ({question['difficulty']})\n"
            f"---\n{problem['translatedContent']}\n---\n🔗 https://leetcode.cn/problems/{slug}"
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
