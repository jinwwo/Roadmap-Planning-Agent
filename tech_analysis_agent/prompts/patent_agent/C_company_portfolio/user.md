도메인: {domain}
분석 기준 연도: {reference_year}
카테고리 힌트: {category_hints}

[우리 기업]
기업명: {company_name}
기업 정보: {company_profile}

[관련 기업 후보]
{related_companies}

아래는 우리 기업과 관련 기업들의 USPTO 특허 포트폴리오 데이터입니다.
반드시 이 데이터, 특히 관련 기업들의 특허 데이터에서 반복적으로 관찰되는 기술 테마를 근거로 후보 기술을 도출하세요.

[수집된 기업별 특허 데이터]
{patent_raw}

위 데이터를 분석하여 지정된 JSON 포맷으로 출력하세요.

중요:
- 기술 후보군은 "조사한 다른 기업들의 특허 데이터"를 분석해 만들어야 합니다.
- patent_maps에는 actor_similarity_map만 포함하세요.
- actor_similarity_map은 우리 기업을 center_actor로 두고 관련 기업을 related_actor로 연결하는 edge 리스트여야 합니다.
- similarity/edge_weight가 높을수록 렌더링에서 edge가 두껍게 표현됩니다.
