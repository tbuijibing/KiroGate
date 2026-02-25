# -*- coding: utf-8 -*-

"""
配置模块单元测试?

测试模型映射、超时配置和设置验证?
"""

import pytest

from geek_gateway.config import (
    get_internal_model_id,
    get_adaptive_timeout,
    AVAILABLE_MODELS,
    SLOW_MODELS,
    SLOW_MODEL_TIMEOUT_MULTIPLIER,
)


class TestModelMapping:
    """模型映射测试类�?""

    def test_get_internal_model_id_valid_model(self):
        """测试有效模型名返回正确的内部 ID�?""
        # 测试 claude-opus-4-5
        result = get_internal_model_id("claude-opus-4-5")
        assert result == "claude-opus-4.5"
        
        # 测试 claude-sonnet-4-5
        result = get_internal_model_id("claude-sonnet-4-5")
        assert result == "CLAUDE_SONNET_4_5_20250929_V1_0"

    def test_get_internal_model_id_all_available_models(self):
        """测试所有可用模型都能正确映射�?""
        for model in AVAILABLE_MODELS:
            result = get_internal_model_id(model)
            assert result is not None
            assert isinstance(result, str)
            assert len(result) > 0

    def test_get_internal_model_id_invalid_model(self):
        """测试无效模型名抛?ValueError�?""
        with pytest.raises(ValueError) as exc_info:
            get_internal_model_id("invalid-model-name")
        
        assert "不支持的模型" in str(exc_info.value)

    def test_get_internal_model_id_internal_id_passthrough(self):
        """测试内部模型 ID 直接传递�?""
        # 内部 ID 应该直接返回
        result = get_internal_model_id("claude-opus-4.5")
        assert result == "claude-opus-4.5"
        
        # 测试另一个内?ID
        result = get_internal_model_id("claude-haiku-4.5")
        assert result == "claude-haiku-4.5"

    def test_get_internal_model_id_empty_string(self):
        """测试空字符串抛出 ValueError�?""
        with pytest.raises(ValueError):
            get_internal_model_id("")

    def test_get_internal_model_id_case_sensitive(self):
        """测试模型名称区分大小写�?""
        # 大写应该失败
        with pytest.raises(ValueError):
            get_internal_model_id("CLAUDE-OPUS-4-5")


class TestAdaptiveTimeout:
    """自适应超时测试类�?""

    def test_get_adaptive_timeout_slow_model(self):
        """测试慢模型返回增加的超时时间�?""
        base_timeout = 60.0
        
        # 测试 opus 模型
        result = get_adaptive_timeout("claude-opus-4-5", base_timeout)
        expected = base_timeout * SLOW_MODEL_TIMEOUT_MULTIPLIER
        
        assert result == expected

    def test_get_adaptive_timeout_normal_model(self):
        """测试普通模型返回基础超时时间�?""
        base_timeout = 60.0
        
        # 测试 sonnet 模型
        result = get_adaptive_timeout("claude-sonnet-4-5", base_timeout)
        
        assert result == base_timeout

    def test_get_adaptive_timeout_haiku_model(self):
        """测试 haiku 模型返回基础超时时间�?""
        base_timeout = 60.0
        
        result = get_adaptive_timeout("claude-haiku-4-5", base_timeout)
        
        assert result == base_timeout

    def test_get_adaptive_timeout_empty_model(self):
        """测试空模型名返回基础超时时间�?""
        base_timeout = 60.0
        
        result = get_adaptive_timeout("", base_timeout)
        
        assert result == base_timeout

    def test_get_adaptive_timeout_none_model(self):
        """测试 None 模型名返回基础超时时间�?""
        base_timeout = 60.0
        
        result = get_adaptive_timeout(None, base_timeout)
        
        assert result == base_timeout

    def test_get_adaptive_timeout_case_insensitive(self):
        """测试模型名称不区分大小写�?""
        base_timeout = 60.0
        expected = base_timeout * SLOW_MODEL_TIMEOUT_MULTIPLIER
        
        # 大写应该也能识别为慢模型
        result = get_adaptive_timeout("CLAUDE-OPUS-4-5", base_timeout)
        
        assert result == expected


class TestSettingsValidation:
    """设置验证测试类�?""

    def test_validate_log_level_valid(self):
        """测试有效的日志级别�?""
        from geek_gateway.config import Settings
        
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        for level in valid_levels:
            result = Settings.validate_log_level(level)
            assert result == level

    def test_validate_log_level_lowercase(self):
        """测试小写日志级别被转换为大写�?""
        from geek_gateway.config import Settings
        
        result = Settings.validate_log_level("debug")
        assert result == "DEBUG"

    def test_validate_debug_mode_valid(self):
        """测试有效?debug_mode 值�?""
        from geek_gateway.config import Settings
        
        valid_modes = ["off", "errors", "all"]
        for mode in valid_modes:
            result = Settings.validate_debug_mode(mode)
            assert result == mode

    def test_validate_debug_mode_invalid(self):
        """测试无效?debug_mode 值返回默�?'off'�?""
        from geek_gateway.config import Settings
        
        # 根据实际实现，无效值会返回 "off" 而不是抛出异?
        result = Settings.validate_debug_mode("invalid")
        
        assert result == "off"
