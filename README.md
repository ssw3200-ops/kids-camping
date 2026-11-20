# 아이랑 캠핑 한 번에 🏕️

블로그 [아이랑 오늘 뭐하지?](https://blog.naver.com/todaywithkids) 이웃 전용 도구.
아이 시설 있는 캠핑장 + 그날 날씨(밤 최저기온) + 근처 응급실·소아과·약국 + 편의점·마트 + 주변 가볼 곳·행사를 한 화면에.

- 페이지: https://ssw3200-ops.github.io/kids-camping/ (비밀번호)
- 결과 확인: https://ssw3200-ops.github.io/kids-camping/status.json
- 매일 06:37 자동 갱신 (캠핑장·병원·가게·관광지는 7일마다)
- Secrets: `DATA_GO_KR_KEY`, `PAGE_PASSWORD`
- Pages 설정: Deploy from a branch → `gh-pages` / (root)
- 키 없이 화면 확인: `python src/build.py --out site --sample` (비밀번호 1234)
