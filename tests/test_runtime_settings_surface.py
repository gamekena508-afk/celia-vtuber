import os
import pytest

pytestmark = pytest.mark.skipif(os.getenv("CELIA_LIVE_UI_TEST") != "1", reason="requires explicitly authorized live browser test")
pytest.importorskip("selenium")
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


def test_agent_retains_controls_and_runtime_subpage():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    driver = webdriver.Chrome(options=options)
    try:
        driver.get("http://127.0.0.1:12393/?corpo=2d")
        wait = WebDriverWait(driver, 15)
        wait.until(lambda d: d.execute_script("return document.querySelectorAll('button').length > 10"))
        driver.execute_script("document.querySelectorAll('button')[3].click()")
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '[role="dialog"]'))
        wait.until(
            lambda d: d.execute_script(
                "return document.querySelector('[role=tab][data-value=agent]')?.textContent === 'Agent'"
            )
        )
        driver.execute_script("document.querySelector('[role=tab][data-value=agent]').click()")
        wait.until(lambda d: d.execute_script("return !!document.querySelector('[data-celia-v3-target=celia-v3-runtime]')"))
        driver.execute_script("document.querySelector('[data-celia-v3-target=celia-v3-runtime]').click()")
        wait.until(
            lambda d: d.execute_script(
                "return document.getElementById('celia-runtime-settings')?.textContent.includes('Modelo de conversa')"
            )
        )
        state = driver.execute_script(
            "const panel = document.getElementById('celia-runtime-settings');"
            "const legacy = document.querySelector('[role=tabpanel][id$=content-agent] > :first-child');"
            "return { text: panel.textContent, legacyHidden: legacy.hidden, tab: document.querySelector('[role=tab][data-value=agent]').textContent };"
        )
        assert state["tab"] == "Agent"
        assert "Modelo de conversa" in state["text"]
        assert "Perfil conversacional" in state["text"]
        assert state["legacyHidden"] is False
    finally:
        driver.quit()
