"""Multi-body assembly skills.

이 카테고리의 skill 은 ``TopoDS_Compound`` 를 container 로 사용해 여러 named
sub-solid 를 한 body 에 담아 다룬다. component 이름은 SkillResult.extras
``component_names`` (list[str]) 에 sub-shape 순서와 평행하게 저장된다.

Skills
------
add_component       — 외부 STEP 또는 현재 body 를 transform 후 compound 에 추가
move_component      — 이름으로 component 를 찾아 translate/rotate
interference_check  — 모든 (or 지정된) pair 의 boolean overlap 검사
mate_planar         — face A 를 face B 에 평행/anti-parallel 로 정렬 + offset
"""
from phone_designer.skills.assembly.add_component import AddComponent
from phone_designer.skills.assembly.interference_check import InterferenceCheck
from phone_designer.skills.assembly.mate_planar import MatePlanar
from phone_designer.skills.assembly.move_component import MoveComponent

__all__ = [
    "AddComponent",
    "MoveComponent",
    "InterferenceCheck",
    "MatePlanar",
]
