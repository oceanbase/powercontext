# 测试覆盖矩阵 — powermem issue batch fix

## AC 覆盖

| AC ID | 需求描述 | 测试文件 | 测试函数 | 状态 |
|-------|---------|---------|---------|------|
| AC-1.1 | div className 不包含 undefined | test_fc1_css_class.py | test_ac_1_1_no_dynamic_icon_class_lookup | 🔴 |
| AC-1.1 | div className 使用静态 styles.icon | test_fc1_css_class.py | test_ac_1_1_classname_uses_static_icon | 🔴 |
| AC-1.1 | 无模板字面量拼接 styles 动态查找 | test_fc1_css_class.py | test_ac_1_1_no_undefined_in_classname | 🔴 |
| AC-1.2 | 5 个 feature key 定义 | test_fc1_css_class.py | test_ac_1_2_all_five_features_defined | 🔴 |
| AC-1.2 | .icon class 包含完整样式 | test_fc1_css_class.py | test_ac_1_2_icon_class_has_styles | 🔴 |
| AC-1.3 | 响应式 media queries | test_fc1_css_class.py | test_ac_1_3_responsive_media_queries | 🔴 |
| AC-2.1 | import NimRerank 成功 | test_fc2_nim_rerank_export.py | test_ac_2_1_import_nim_rerank | 🔴 |
| AC-2.2 | import NimRerankConfig 成功 | test_fc2_nim_rerank_export.py | test_ac_2_2_import_nim_rerank_config | 🔴 |
| AC-2.3 | __all__ 包含 NIM 导出 | test_fc2_nim_rerank_export.py | test_ac_2_3_all_contains_nim_exports | 🔴 |
| AC-2.3 | star import 包含 NIM | test_fc2_nim_rerank_export.py | test_ac_2_3_star_import_includes_nim | 🔴 |
| AC-2.4 | 模块属性包含 NIM 类 | test_fc2_nim_rerank_export.py | test_ac_2_4_module_contains_nim_attributes | 🔴 |
| AC-3.1 | metadata 包含 should_forget | test_fc3_forget_marker.py | test_ac_3_1_metadata_contains_should_forget | 🔴 |
| AC-3.2 | metadata 包含 marked_for_forgetting_at | test_fc3_forget_marker.py | test_ac_3_2_metadata_contains_marked_for_forgetting_at | 🔴 |
| AC-3.3 | top-level 字段保留（SQLite 兼容） | test_fc3_forget_marker.py | test_ac_3_3_top_level_fields_preserved | 🔴 |
| AC-3.3 | SQLite 回归不破坏 | test_fc3_forget_marker.py | test_ac_3_3_sqlite_regression_no_break | 🔴 |
| AC-3.4 | updates dict 包含遗忘标记 | test_fc3_forget_marker.py | test_ac_3_4_forget_marker_in_updates_dict | 🔴 |
| AC-4.1 | multi-agent retention_score 非 null | test_fc4_retention_score.py | test_ac_4_1_retention_score_not_null | 🔴 |
| AC-4.2 | retention_score 值正确 | test_fc4_retention_score.py | test_ac_4_2_retention_score_value_correct | 🔴 |
| AC-4.2 | multi-user retention_score 非 null | test_fc4_retention_score.py | test_ac_4_2_multi_user_retention_score_not_null | 🔴 |
| AC-4.4 | LLM 未启用默认值 1.0 | test_fc4_retention_score.py | test_ac_4_4_default_retention_score_when_no_intelligence | 🔴 |
| AC-4.4 | multi-user 默认值 1.0 | test_fc4_retention_score.py | test_ac_4_4_multi_user_default_retention | 🔴 |
| AC-5.1 | memory_service 无 str(e) 泄露 | test_fc5_api_security.py | test_ac_5_1_memory_service_no_str_e | 🔴 |
| AC-5.1 | user_service 无 str(e) 泄露 | test_fc5_api_security.py | test_ac_5_1_user_service_no_str_e | 🔴 |
| AC-5.1 | agent_service 无 str(e) 泄露 | test_fc5_api_security.py | test_ac_5_1_agent_service_no_str_e | 🔴 |
| AC-5.1 | search_service 无 str(e) 泄露 | test_fc5_api_security.py | test_ac_5_1_search_service_no_str_e | 🔴 |
| AC-5.2 | logger 仍记录原始异常 | test_fc5_api_security.py | test_ac_5_2_logger_still_logs_original_exception | 🔴 |
| AC-5.3 | health check 使用通用消息 | test_fc5_api_security.py | test_ac_5_3_health_check_no_raw_exception | 🔴 |
| AC-5.4 | memory_service 使用安全消息 | test_fc5_api_security.py | test_ac_5_4_memory_service_uses_safe_messages | 🔴 |
| AC-5.4 | user_service 使用安全消息 | test_fc5_api_security.py | test_ac_5_4_user_service_uses_safe_messages | 🔴 |
| AC-5.4 | agent_service 使用安全消息 | test_fc5_api_security.py | test_ac_5_4_agent_service_uses_safe_messages | 🔴 |
| AC-5.4 | search_service 使用安全消息 | test_fc5_api_security.py | test_ac_5_4_search_service_uses_safe_messages | 🔴 |
| AC-5.5 | ErrorResponse 结构验证 | test_fc5_api_security.py | test_ac_5_5_error_response_structure | 🔴 |
| AC-5.1 | batch_ingest 无 str(e) | test_fc5_api_security.py | test_ac_5_1_batch_ingest_no_str_e | 🔴 |
| AC-5.3 | health_check database 无原始 str | test_fc5_api_security.py | test_ac_5_3_health_check_database_no_raw_str | 🔴 |
| AC-5.3 | health_check LLM 无原始 str | test_fc5_api_security.py | test_ac_5_3_health_check_llm_no_raw_str | 🔴 |
| AC-6.1 | 调用六个 _evaluate_* 方法 | test_fc6_importance_eval.py | test_ac_6_1_calls_all_six_dimensions | 🔴 |
| AC-6.2 | 加权和计算正确 | test_fc6_importance_eval.py | test_ac_6_2_weighted_sum_calculation | 🔴 |
| AC-6.2 | 返回值 clamp 到 [0,1] | test_fc6_importance_eval.py | test_ac_6_2_clamped_to_unit_range | 🔴 |
| AC-6.3 | breakdown 包含 weighted_total | test_fc6_importance_eval.py | test_ac_6_3_breakdown_contains_weighted_total | 🔴 |
| AC-6.3 | weighted_total 值正确 | test_fc6_importance_eval.py | test_ac_6_3_weighted_total_matches_calculation | 🔴 |
| AC-6.4 | 规则与 LLM 使用相同框架 | test_fc6_importance_eval.py | test_ac_6_4_rule_based_uses_same_framework_as_llm | 🔴 |
| AC-6.5 | 零输入返回 0.0 | test_fc6_importance_eval.py | test_ac_6_5_zero_input_returns_zero | 🔴 |
| AC-6.5 | 空内容返回 0.0 | test_fc6_importance_eval.py | test_ac_6_5_empty_content_returns_zero | 🔴 |
| AC-7.1 | multi-agent 使用 Ebbinghaus | test_fc7_ebbinghaus_decay.py | test_ac_7_1_multi_agent_uses_ebbinghaus | 🔴 |
| AC-7.2 | multi-user 使用 Ebbinghaus | test_fc7_ebbinghaus_decay.py | test_ac_7_2_multi_user_uses_ebbinghaus | 🔴 |
| AC-7.3 | working 衰减快于 long_term | test_fc7_ebbinghaus_decay.py | test_ac_7_3_working_decays_faster_than_long_term | 🔴 |
| AC-7.3 | 指数衰减非线性 | test_fc7_ebbinghaus_decay.py | test_ac_7_3_exponential_not_linear | 🔴 |
| AC-7.4 | 强化降低衰减速度 | test_fc7_ebbinghaus_decay.py | test_ac_7_4_reinforcement_slows_decay | 🔴 |
| AC-7.4 | 强化因子生效 | test_fc7_ebbinghaus_decay.py | test_ac_7_4_reinforcement_factor_applied | 🔴 |
| AC-7.5 | 未初始化时回退 | test_fc7_ebbinghaus_decay.py | test_ac_7_5_fallback_when_not_initialized | 🔴 |
| AC-7.5 | 默认配置值 | test_fc7_ebbinghaus_decay.py | test_ac_7_5_default_config_values | 🔴 |
| AC-7.6 | 与 IntelligentManager 算法一致 | test_fc7_ebbinghaus_decay.py | test_ac_7_6_same_algorithm_as_intelligent_manager | 🔴 |
| AC-7.6 | 公式正确性 | test_fc7_ebbinghaus_decay.py | test_ac_7_6_formula_correctness | 🔴 |

**AC 覆盖统计**: 53 个测试覆盖 28 个 AC（部分 AC 有多个测试场景）

---

## NFR 覆盖

| NFR ID | 描述 | 测试文件 | 测试函数 | 状态 |
|--------|------|---------|---------|------|
| NFR-1.1 | FC-1 视觉兼容 | test_fc1_css_class.py | test_nfr_1_1_visual_compatibility | 🔴 |
| NFR-1.2 | FC-1 最小变更 | test_fc1_css_class.py | test_nfr_1_2_minimal_change | 🔴 |
| NFR-1.3 | FC-1 响应式保留 | test_fc1_css_class.py | test_nfr_1_3_responsive_preserved | 🔴 |
| NFR-2.1 | FC-2 向后兼容 | test_fc2_nim_rerank_export.py | test_nfr_2_1_backward_compatible | 🔴 |
| NFR-2.2 | FC-2 依赖安全 | test_fc2_nim_rerank_export.py | test_nfr_2_2_dependency_safe | 🔴 |
| NFR-2.3 | FC-2 导入顺序 | test_fc2_nim_rerank_export.py | test_nfr_2_3_import_ordering | 🔴 |
| NFR-3.1 | FC-3 向后兼容 | test_fc3_forget_marker.py | test_nfr_3_1_backward_compatible | 🔴 |
| NFR-3.2 | FC-3 最小变更 | test_fc3_forget_marker.py | test_nfr_3_2_minimal_change | 🔴 |
| NFR-3.3 | FC-3 数据完整性 | test_fc3_forget_marker.py | test_nfr_3_3_data_integrity | 🔴 |
| NFR-4.3 | FC-4 数据质量 | test_fc4_retention_score.py | test_nfr_4_3_data_quality | 🔴 |
| NFR-5.1 | FC-5 无敏感信息泄露 | test_fc5_api_security.py | test_nfr_5_1_no_sensitive_info_in_api_response | 🔴 |
| NFR-5.2 | FC-5 可观测性保留 | test_fc5_api_security.py | test_nfr_5_2_observability_preserved | 🔴 |
| NFR-5.3 | FC-5 结构向后兼容 | test_fc5_api_security.py | test_nfr_5_3_backward_compatible_structure | 🔴 |
| NFR-6.1 | FC-6 返回范围 [0,1] | test_fc6_importance_eval.py | test_nfr_6_1_return_range_preserved | 🔴 |
| NFR-6.2 | FC-6 框架一致性 | test_fc6_importance_eval.py | test_nfr_6_2_consistency_with_llm_framework | 🔴 |
| NFR-6.3 | FC-6 权重可配置 | test_fc6_importance_eval.py | test_nfr_6_3_configurable_weights | 🔴 |
| NFR-6.4 | FC-6 零输入 | test_fc6_importance_eval.py | test_nfr_6_4_zero_input | 🔴 |
| NFR-7.1 | FC-7 方法签名不变 | test_fc7_ebbinghaus_decay.py | test_nfr_7_1_method_signature_preserved | 🔴 |
| NFR-7.2 | FC-7 算法一致性 | test_fc7_ebbinghaus_decay.py | test_nfr_7_2_algorithm_consistency | 🔴 |
| NFR-7.3 | FC-7 可配置参数 | test_fc7_ebbinghaus_decay.py | test_nfr_7_3_configurable_params | 🔴 |
| NFR-7.4 | FC-7 性能 O(1) | test_fc7_ebbinghaus_decay.py | test_nfr_7_4_performance_constant_time | 🔴 |
| NFR-7.5 | FC-7 错误容错 | test_fc7_ebbinghaus_decay.py | test_nfr_7_5_error_tolerance | 🔴 |

**NFR 覆盖统计**: 22 个测试覆盖 22 个 NFR

---

## 测试汇总

| 测试文件 | FC | 测试数 | AC 覆盖 | NFR 覆盖 |
|---------|-----|--------|---------|----------|
| test_fc1_css_class.py | FC-1 | 6 | 3 AC (1.1-1.3) | 3 NFR |
| test_fc2_nim_rerank_export.py | FC-2 | 5 | 4 AC (2.1-2.4) | 3 NFR |
| test_fc3_forget_marker.py | FC-3 | 5 | 4 AC (3.1-3.4) | 3 NFR |
| test_fc4_retention_score.py | FC-4 | 5 | 4 AC (4.1-4.4) | 1 NFR |
| test_fc5_api_security.py | FC-5 | 14 | 5 AC (5.1-5.5) | 3 NFR |
| test_fc6_importance_eval.py | FC-6 | 8 | 5 AC (6.1-6.5) | 4 NFR |
| test_fc7_ebbinghaus_decay.py | FC-7 | 10 | 6 AC (7.1-7.6) | 5 NFR |
| **总计** | **7 FC** | **53** | **28 AC** | **22 NFR** |

---

## 图例

- 🔴 红灯状态（预期失败 — 实现代码尚未修改）
- 🟢 绿灯状态（测试通过 — 实现代码已修复）
