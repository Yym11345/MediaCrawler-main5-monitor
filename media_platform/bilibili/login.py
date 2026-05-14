# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/bilibili/login.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。


# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2023/12/2 18:44
# @Desc    : bilibili login implementation class

import asyncio
import functools
from typing import Optional

from playwright.async_api import BrowserContext, Page
from tenacity import (RetryError, retry, retry_if_result, stop_after_attempt,
                      wait_fixed)

import config
from base.base_crawler import AbstractLogin
from tools import utils


class BilibiliLogin(AbstractLogin):
    def __init__(self,
                 login_type: str,
                 browser_context: BrowserContext,
                 context_page: Page,
                 login_phone: Optional[str] = "",
                 cookie_str: str = ""
                 ):
        config.LOGIN_TYPE = login_type
        self.browser_context = browser_context
        self.context_page = context_page
        self.login_phone = login_phone
        self.cookie_str = cookie_str

    async def begin(self):
        """Start login bilibili"""
        utils.logger.info("[BilibiliLogin.begin] Begin login Bilibili ...")
        if config.LOGIN_TYPE == "qrcode":
            await self.login_by_qrcode()
        elif config.LOGIN_TYPE == "phone":
            await self.login_by_mobile()
        elif config.LOGIN_TYPE == "cookie":
            await self.login_by_cookies()
        else:
            raise ValueError(
                "[BilibiliLogin.begin] Invalid Login Type Currently only supported qrcode or phone or cookie ...")

    @retry(stop=stop_after_attempt(600), wait=wait_fixed(1), retry=retry_if_result(lambda value: value is False))
    async def check_login_state(self) -> bool:
        """
            Check if the current login status is successful and return True otherwise return False
            retry decorator will retry 20 times if the return value is False, and the retry interval is 1 second
            if max retry times reached, raise RetryError
        """
        current_cookie = await self.browser_context.cookies()
        _, cookie_dict = utils.convert_cookies(current_cookie)
        if cookie_dict.get("SESSDATA", "") or cookie_dict.get("DedeUserID"):
            return True
        return False

    async def login_by_qrcode(self):
        """login bilibili website and keep webdriver login state"""
        utils.logger.info("[BilibiliLogin.login_by_qrcode] Begin login bilibili by qrcode ...")

        # Wait for page to fully load before interacting
        try:
            await self.context_page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            utils.logger.info("[BilibiliLogin.login_by_qrcode] networkidle timeout, proceeding anyway")
        await asyncio.sleep(1)

        utils.logger.info(f"[BilibiliLogin.login_by_qrcode] Page URL: {self.context_page.url}")

        # Try multiple selectors — Bilibili may change DOM structure
        qrcode_selectors = [
            "//div[@class='login-scan-box']//img",
            "//img[contains(@src,'qrcode') or contains(@src,'QR')]",
            "//div[contains(@class,'qrcode')]//img",
            "//div[contains(@class,'login-scan')]//img",
        ]

        base64_qrcode_img = ""
        for sel in qrcode_selectors:
            base64_qrcode_img = await self._find_qrcode_with_wait(sel, timeout_ms=5000)
            if base64_qrcode_img:
                utils.logger.info(f"[BilibiliLogin.login_by_qrcode] Found QR code with selector: {sel}")
                break

        if not base64_qrcode_img:
            # QR code not visible yet — need to click login button
            # Close any blocking mask overlays
            try:
                mask = self.context_page.locator(".bili-mini-mask")
                if await mask.count() > 0:
                    close_btn = self.context_page.locator(".bili-mini-mask .bili-mini-close")
                    if await close_btn.count() > 0:
                        await close_btn.first.click()
                        await asyncio.sleep(0.5)
                    else:
                        await self.context_page.evaluate(
                            "document.querySelectorAll('.bili-mini-mask').forEach(el => el.remove())"
                        )
                        await asyncio.sleep(0.3)
                    utils.logger.info("[BilibiliLogin.login_by_qrcode] Removed mask overlay")
            except Exception as e:
                utils.logger.info(f"[BilibiliLogin.login_by_qrcode] Mask handling: {e}")

            # click login button (use force to bypass any remaining overlay)
            login_button_ele = self.context_page.locator(
                "xpath=//div[@class='right-entry__outside go-login-btn']//div"
            )
            try:
                await login_button_ele.click(timeout=5000)
                utils.logger.info("[BilibiliLogin.login_by_qrcode] Clicked login button")
            except Exception as e:
                utils.logger.info(f"[BilibiliLogin.login_by_qrcode] Normal click failed: {e}, trying force click")
                try:
                    await login_button_ele.click(force=True)
                    utils.logger.info("[BilibiliLogin.login_by_qrcode] Force click succeeded")
                except Exception as e2:
                    utils.logger.info(f"[BilibiliLogin.login_by_qrcode] Force click also failed: {e2}")
            await asyncio.sleep(3)

            # Retry finding QR code after clicking
            for sel in qrcode_selectors:
                base64_qrcode_img = await self._find_qrcode_with_wait(sel, timeout_ms=8000)
                if base64_qrcode_img:
                    utils.logger.info(f"[BilibiliLogin.login_by_qrcode] Found QR code after click with selector: {sel}")
                    break

        if not base64_qrcode_img:
            # Last resort: dump page content for debugging
            try:
                html_snippet = await self.context_page.evaluate(
                    "document.querySelector('.bili-mini-login')?.innerHTML?.substring(0, 500) || 'no login panel found'"
                )
                utils.logger.info(f"[BilibiliLogin.login_by_qrcode] Login panel HTML: {html_snippet}")
            except Exception:
                pass
            raise RuntimeError(
                "[BilibiliLogin.login_by_qrcode] Login failed: could not find QR code. "
                "Please check if Bilibili login page structure has changed."
            )

        # show login qrcode
        partial_show_qrcode = functools.partial(utils.show_qrcode, base64_qrcode_img)
        asyncio.get_running_loop().run_in_executor(executor=None, func=partial_show_qrcode)

        utils.logger.info(f"[BilibiliLogin.login_by_qrcode] Waiting for scan code login, remaining time is 20s")
        try:
            await self.check_login_state()
        except RetryError:
            raise RuntimeError("[BilibiliLogin.login_by_qrcode] Login bilibili failed by qrcode login method: timeout waiting for scan")

        wait_redirect_seconds = 5
        utils.logger.info(
            f"[BilibiliLogin.login_by_qrcode] Login successful then wait for {wait_redirect_seconds} seconds redirect ...")
        await asyncio.sleep(wait_redirect_seconds)

    async def _find_qrcode_with_wait(self, selector: str, timeout_ms: int = 5000) -> str:
        """Find QR code image, waiting for src attribute to be populated."""
        try:
            el = await self.context_page.wait_for_selector(
                selector, timeout=timeout_ms, state="visible"
            )
            if not el:
                return ""
            # Wait for src to be populated (not empty, not just "data:,")
            src = await el.get_attribute("src") or ""
            if not src or src == "data:,":
                # src not loaded yet, wait a bit and retry
                await asyncio.sleep(1)
                src = await el.get_attribute("src") or ""
            if not src or src == "data:,":
                return ""
            utils.logger.info(f"[BilibiliLogin._find_qrcode_with_wait] Got src: {src[:80]}...")
            return await utils.find_login_qrcode(self.context_page, selector=selector)
        except Exception as e:
            utils.logger.info(f"[BilibiliLogin._find_qrcode_with_wait] Selector '{selector[:50]}...' failed: {e}")
            return ""

    async def login_by_mobile(self):
        pass

    async def login_by_cookies(self):
        utils.logger.info("[BilibiliLogin.login_by_qrcode] Begin login bilibili by cookie ...")
        for key, value in utils.convert_str_cookie_to_dict(self.cookie_str).items():
            await self.browser_context.add_cookies([{
                'name': key,
                'value': value,
                'domain': ".bilibili.com",
                'path': "/"
            }])
