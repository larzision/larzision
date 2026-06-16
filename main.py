import json
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

@register("atrelay", "miko&哥", "艾特群友", "1.1.0")
class SendToGroupPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 仅新增：消息关联存储（接收方QQ → 发起方QQ）
        self.message_relation = {}
        logger.info("=== SendToGroupPlugin 初始化 ===")

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    @filter.on_llm_request()
    async def on_llm_request_hook(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        在 LLM 请求前修改系统提示词
        """
        # 检查是否是群发消息相关的请求
        message_str = event.message_str
        if "群" in message_str or ("发" in message_str or "讲" in message_str or "说" in message_str or "私聊" in message_str or "发给" in message_str or "告诉" in message_str):
            # 添加隐私保护指令
            privacy_instruction = """
                【重要隐私规则 & 工具调用规则】
                当用户要求你去其他地方(群聊或私聊)发送消息时：
                1. 如果支持工具调用，必须**直接调用 send_to_group_tool 工具**，不要输出自然语言
                4. 如果工具调用失败（例如群不存在、权限不足），你会收到工具返回的错误信息，此时可以输出该错误信息作为最终回复
                5. 如果因为任何原因无法调用工具，请直接输出以下固定格式的错误提示：
               "❌ 发送失败：无法调用工具，请检查机器人权限或群号是否正确"
                6. 禁止在回复中重复或提及你发送的任何内容
                7. 当你收到工具调用结果后，必须改写成贴合当前人设风格的回复，并将这段回复放在 content 字段中。禁止将回复内容放在 reasoning 字段。
                8. **用自己的话重说**：发送到群/私聊的内容必须用自己的话重新组织润色，加语气词，不能生硬复制原话。

                当用户要求**私聊**给某人时，直接调用 send_to_private_user 工具。
                当用户提供**群名**而非群号时，先调用 get_group_id_by_name 获取群号。
                当用户提供**昵称/群备注**而非QQ号时，先调用 get_user_id_by_name 获取QQ号。
                获取ID后再执行发送。

                记住：优先调用工具，工具返回结果后可直接输出结果中的文字；工具调用失败时输出固定错误提示。
            """
            # 添加到系统提示词末尾
            if req.system_prompt:
                req.system_prompt += "\n" + privacy_instruction
            else:
                req.system_prompt = privacy_instruction

            logger.info("已添加隐私保护指令到系统提示词")

    @filter.llm_tool(name="send_to_group_tool")
    async def send_to_group_tool(self, event: AstrMessageEvent, group_id: str, message: str, at_user: str):
        '''
        向指定群聊发送消息。

        Args:
            group_id(string): 目标群号
            message(string): 要发送的话题或指令，例如"讲个笑话"、"天气预报"
            at_user(string): 用于记录要@的用户的QQ号(可选)
        '''
        logger.info(f"工具被调用: group_id={group_id}, message={message}")

        try:
            # 1.验证群号格式
            group_id = str(group_id)
            if not group_id or not group_id.isdigit():
                return "群号格式不正确，请提供有效的群号。"
            # 2.前置检查：确认机器人是否在该群中
            if isinstance(event, AiocqhttpMessageEvent):
                try:
                    # 获取机器人加入的所有群列表
                    group_list = await event.bot.api.call_action("get_group_list")
                    group_ids = [str(g["group_id"]) for g in group_list]
                    if group_id not in group_ids:
                        return f"发送失败：机器人不在群 {group_id} 中，无法发送消息。"
                except Exception as e:
                    logger.warning(f"获取群列表失败，将尝试直接发送: {e}")
                    # 如果获取列表失败，继续尝试发送（可能因权限问题）
            # 3.生成消息内容
            # 从event中获取平台信息
            current_umo = event.unified_msg_origin
            platform = current_umo.split(":")[0]
            target_umo = f"{platform}:GroupMessage:{group_id}"

            #生成带@的主要文本
            At_message = []
            if at_user:
                At_message.append(At(qq=at_user))
                At_message.append(Plain(" "))
            At_message.append(Plain(message))

            # 构建消息链并发送消息
            message_chain = MessageChain(At_message)
            await self.context.send_message(target_umo, message_chain)

            return f"消息已成功发送到群 {group_id}"

        except Exception as e:
            logger.error(f"发送失败: {e}", exc_info=True)
            return f"发送失败: {str(e)}"

    # ==================== 新增：私聊发送用户 ====================
    @filter.llm_tool(name="send_to_private_user")
    async def send_to_private_user(self, event: AstrMessageEvent, user_id: str, message: str):
        '''
        向指定QQ用户发送私聊消息。
        Args:
            user_id(string): 目标QQ号
            message(string): 要发送的消息内容
        '''
        logger.info(f"私聊发送工具调用: user_id={user_id}, message={message}")
        try:
            if not user_id or not user_id.isdigit():
                return "❌ 发送失败：QQ号格式不正确"
            current_umo = event.unified_msg_origin
            platform = current_umo.split(":")[0]
            target_umo = f"{platform}:FriendMessage:{user_id}"
            msg_chain = MessageChain([Plain(message)])
            await self.context.send_message(target_umo, msg_chain)

            # 【官方正确】获取发送者QQ
            from_qq = str(event.message_obj.sender.user_id)
            self.message_relation[user_id] = from_qq
            logger.info(f"记录消息关联: {user_id} <- {from_qq}")

            return f"✅ 已成功向 {user_id} 发送私聊消息"
        except Exception as e:
            logger.error(f"私聊发送失败: {e}", exc_info=True)
            return f"❌ 私聊发送失败: {str(e)}"

    # ==================== 新增：群名获取群号 ====================
    @filter.llm_tool(name="get_group_id_by_name")
    async def get_group_id_by_name(self, event: AstrMessageEvent, group_name: str):
        '''
        根据群名模糊匹配获取群号
        Args:
            group_name(string): 群名关键词
        '''
        try:
            if not isinstance(event, AiocqhttpMessageEvent):
                return "不支持当前平台"
            group_list = await event.bot.api.call_action("get_group_list")
            for g in group_list:
                if group_name in str(g.get("group_name", "")):
                    return str(g["group_id"])
            return "未找到匹配群名"
        except Exception as e:
            logger.error(f"get_group_id_by_name error: {e}")
            return "获取群列表失败"

    # ==================== 新增：群昵称/备注获取QQ号 ====================
    @filter.llm_tool(name="get_user_id_by_name")
    async def get_user_id_by_name(self, event: AstrMessageEvent, group_id: str, nickname: str):
        '''
        根据群内昵称/群备注获取用户QQ号
        Args:
            group_id(string): 群号
            nickname(string): 用户昵称或备注关键词
        '''
        try:
            if not isinstance(event, AiocqhttpMessageEvent):
                return "不支持当前平台"
            members = await event.bot.api.call_action("get_group_member_list", group_id=group_id)
            for m in members:
                nick = str(m.get("nickname", ""))
                card = str(m.get("card", ""))
                if nickname in nick or nickname in card:
                    return str(m["user_id"])
            return "未找到该用户"
        except Exception as e:
            logger.error(f"get_user_id_by_name error: {e}")
            return "获取成员失败"

    @filter.llm_tool(name="get_specified_group_members")
    async def get_specified_group_members(self, event: AstrMessageEvent, group_id: str = "", keyword: str = "") -> str:
        """
        供 LLM 调用的工具：获取指定群聊的成员列表。

        Args:
            group_id(string): 目标群号，为空时默认使用当前群
            keyword(string): 搜索关键词，支持匹配昵称、群名片或QQ号。若为空则返回全员。
        """
        start_time = time.time()

        # 获取群组 ID，如果不在群聊中则返回错误
        target_group_id = group_id if group_id else event.get_group_id()

        if not target_group_id:
            return json.dumps({"status": "error", "message": "未指定群号且当前不在群聊环境中，无法查询成员。"}, ensure_ascii=False)

        # 检查当前消息事件是否支持（目前主要支持 aiocqhttp 协议，即 OneBot）
        if not isinstance(event, AiocqhttpMessageEvent):
            return json.dumps({"status": "error", "message": "当前平台协议暂不支持获取群成员。"}, ensure_ascii=False)

        try:
            # 通过机器人 API 获取群成员原始数据
            raw_members = await event.bot.api.call_action("get_group_member_list", group_id=group_id)
            if not raw_members:
                return json.dumps({"status": "error", "message": "无法获取成员列表或机器人权限不足。"}, ensure_ascii=False)

            formatted_members = []

            for m in raw_members:
                user_id = str(m.get("user_id", ""))
                nickname = m.get("nickname", "")
                card = m.get("card", "") # 群名片（备注）
                role = m.get("role", "member") # 角色：owner(群主), admin(管理员), member(普通成员)

                # 如果提供了关键词，则在 ID、昵称、名片中进行模糊匹配
                search_content = f"{user_id}{nickname}{card}"
                if keyword and keyword not in search_content:
                    continue

                # 角色名称转换
                role_map = {"owner": "群主", "admin": "管理员", "member": "成员"}
                role_cn = role_map.get(role, "成员")

                formatted_members.append({
                    "user_id": user_id,
                    "nickname": nickname,
                    "group_card": card if card else "无",
                    "role": role_cn
                })

            # 构建返回给 LLM 的 JSON 结果
            output_data = {
                "status": "success",
                "group_id": group_id,
                "count": len(formatted_members),
                "members": formatted_members
            }

            logger.debug(f"群成员查询成功：耗时 {time.time() - start_time:.2f}s，共找到 {len(formatted_members)} 人")
            return json.dumps(output_data, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"查询群成员过程发生异常: {e}")
            return json.dumps({"status": "error", "message": f"系统内部异常: {str(e)}"}, ensure_ascii=False)


    @filter.on_llm_response()
    async def on_llm_response_hook(self, event: AstrMessageEvent, resp: LLMResponse):
        """
        精简和优化 LLM 对工具调用结果的回复，去除冗余内容，只保留核心信息。
        """
        # 1. 获取 LLM 生成的原始回复文本
        original_reply = resp.completion_text

        # 2. 如果返回为空(可能模型返回空content)，则设置默认错误提示
        if not original_reply or not original_reply.strip():
            resp.completion_text = "抱歉，我无法处理这个请求（可能是模型响应为空）。"
            logger.warning("LLM 返回了空回复，已替换为默认错误提示。")
            return

        # 3. 定义你的工具返回结果的特征
        success_marker = "消息已成功发送到群"
        error_marker = "发送失败"
        private_success = "已成功向"
        private_error = "私聊发送失败"

        # 3. 判断回复是否与你的工具调用相关
        if success_marker in original_reply or error_marker in original_reply or private_success in original_reply or private_error in original_reply:
            lines = original_reply.strip().split("\n")
            simplified_reply = original_reply
            for line in lines:
                if success_marker in line or error_marker in line or private_success in line or private_error in line:
                    simplified_reply = line.strip()
                    break

            logger.info(f"原始 LLM 回复: {original_reply}")
            logger.info(f"精简后的回复: {simplified_reply}")
            resp.completion_text = simplified_reply

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""