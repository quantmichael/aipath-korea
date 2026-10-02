from __future__ import annotations

import unittest

from collect.collectors.api import _is_relevant_bizinfo_row


class BizinfoRelevanceTests(unittest.TestCase):
    def test_accepts_explicit_ai_opportunity(self) -> None:
        row = {
            "pblancNm": "AI 역량강화 교육 참여기업 모집",
            "bsnsSumryCn": "채용 청년을 위한 인공지능 실무 교육을 지원합니다.",
        }

        self.assertTrue(_is_relevant_bizinfo_row(row))

    def test_accepts_specific_data_analysis_opportunity(self) -> None:
        row = {
            "pblancNm": "데이터 분석 교육생 모집",
            "bsnsSumryCn": "실무 데이터 분석 과정을 운영합니다.",
        }

        self.assertTrue(_is_relevant_bizinfo_row(row))

    def test_rejects_generic_digital_marketing(self) -> None:
        row = {
            "pblancNm": "소상공인 디지털 마케팅 지원사업",
            "bsnsSumryCn": "온라인 판로와 홍보 콘텐츠 제작을 지원합니다.",
        }

        self.assertFalse(_is_relevant_bizinfo_row(row))

    def test_rejects_generic_smart_facility(self) -> None:
        row = {
            "pblancNm": "스마트 HACCP 등록시스템 구축 지원",
            "bsnsSumryCn": "식품안전 기록관리 시스템 비용을 지원합니다.",
        }

        self.assertFalse(_is_relevant_bizinfo_row(row))

    def test_rejects_generic_ict_award(self) -> None:
        row = {
            "pblancNm": "정보통신 중소기업 발전 유공자 포상",
            "hashtags": "ICT,정보통신,중소기업,포상",
        }

        self.assertFalse(_is_relevant_bizinfo_row(row))


if __name__ == "__main__":
    unittest.main()
