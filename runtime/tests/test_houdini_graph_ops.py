from __future__ import annotations

import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ADAPTER_PY=ROOT/"houdini_adapter"/"python"
if str(ADAPTER_PY) not in sys.path:
    sys.path.insert(0,str(ADAPTER_PY))

from ai_bridge_houdini import error_ops, graph_ops
from ai_bridge_houdini.client import CAPABILITIES


class FakeParm:
    def __init__(self,value=0):
        self.value=value
    def set(self,value):
        self.value=value


class FakeParmTemplate:
    def __init__(self,name):
        self._name=name
    def name(self):
        return self._name


class FakeParmGroup:
    def entriesWithoutFolders(self):
        return [FakeParmTemplate("value"),FakeParmTemplate("scale")]


class FakeType:
    def __init__(self,name="null"):
        self._name=name
    def name(self):
        return self._name
    def nameWithCategory(self):
        return "Sop/"+self._name
    def parmTemplateGroup(self):
        return FakeParmGroup()


class FakeCategory:
    def nodeTypes(self):
        return {"null":FakeType("null"),"box":FakeType("box")}


class FakeColor:
    def __init__(self,rgb=(0.8,0.8,0.8)):
        self._rgb=tuple(rgb)
    def rgb(self):
        return self._rgb


class FakeNode:
    def __init__(self,hou,path,node_type="null",parent=None):
        self.hou=hou
        self._path=path
        self._name=path.rsplit("/",1)[-1]
        self._type=FakeType(node_type)
        self.parent=parent
        self._parms={"value":FakeParm(),"scale":FakeParm()}
        self._position=(0.0,0.0)
        self._comment=""
        self._color=FakeColor()
        self._inputs=[]
        self.destroyed=False

    def path(self): return self._path
    def name(self): return self._name
    def type(self): return self._type
    def parm(self,name): return self._parms.get(name)
    def childTypeCategory(self): return FakeCategory()
    def node(self,name):
        path=self._path.rstrip("/")+"/"+name
        return self.hou.nodes.get(path)
    def createNode(self,node_type,node_name):
        path=self._path.rstrip("/")+"/"+node_name
        node=FakeNode(self.hou,path,node_type,parent=self)
        self.hou.nodes[path]=node
        return node
    def setInput(self,index,source,output_index=0):
        while len(self._inputs)<=index:
            self._inputs.append(None)
        self._inputs[index]=(source,output_index)
    def setPosition(self,value): self._position=tuple(value)
    def position(self): return self._position
    def setComment(self,value): self._comment=str(value)
    def setColor(self,value): self._color=value
    def layoutChildren(self,nodes): self.hou.layout_calls+=1
    def destroy(self):
        self.destroyed=True
        self.hou.nodes.pop(self._path,None)


class FakeHou:
    Color=FakeColor
    def __init__(self):
        self.nodes={}
        self.layout_calls=0
        self.nodes["/obj"]=FakeNode(self,"/obj","obj")
    def node(self,path):
        return self.nodes.get(path)


def test_pack2_capabilities_registered():
    names={item["name"] for item in CAPABILITIES}
    assert {"node.create_configured","network.validate","network.apply"} <= names


def test_network_validate_rejects_bad_type_and_parameter():
    hou=FakeHou()
    bad_type=graph_ops.validate_spec(hou,"/obj",[{"id":"x","type":"does_not_exist","name":"x"}],[])
    assert bad_type["ok"] is False
    assert bad_type["errors"][0]["code"]=="NODE_TYPE_NOT_FOUND"

    bad_parm=graph_ops.validate_spec(hou,"/obj",[{"id":"x","type":"null","name":"x","parms":{"missing":1}}],[])
    assert bad_parm["ok"] is False
    assert bad_parm["errors"][0]["code"]=="PARM_NOT_FOUND"


def test_create_configured_sets_parms_state_and_input():
    hou=FakeHou()
    source=hou.nodes["/obj"].createNode("null","source")
    result=graph_ops.create_configured(
        hou,
        "/obj",
        "null",
        "target",
        parms={"value":7},
        state={"position":[2.0,3.0],"comment":"bridge"},
        inputs=[{"source":"/obj/source","output":0}],
    )
    target=hou.node("/obj/target")
    assert result["verified"] is True
    assert target.parm("value").value==7
    assert target.position()==(2.0,3.0)
    assert target._comment=="bridge"
    assert target._inputs[0][0] is source


def test_network_apply_builds_graph_in_one_operation():
    hou=FakeHou()
    result=graph_ops.apply_spec(
        hou,
        "/obj",
        [
            {"id":"a","type":"null","name":"A","parms":{"value":1}},
            {"id":"b","type":"null","name":"B","parms":{"value":2}},
        ],
        [{"source":"a","target":"b","input":0}],
        layout=True,
    )
    assert result["verified"] is True
    assert result["refs"]=={"a":"/obj/A","b":"/obj/B"}
    assert hou.node("/obj/B")._inputs[0][0] is hou.node("/obj/A")
    assert hou.layout_calls==1


def test_error_schema_classifies_common_failures():
    failure=error_ops.classify_exception(
        ValueError("Parameter not found: /obj/A::missing"),
        operation="node.create_configured",
        arguments={"parent":"/obj","node_type":"null","name":"A"},
    )
    assert failure["code"]=="PARM_NOT_FOUND"
    assert failure["category"]=="invalid_parameter"
    assert failure["context"]["operation"]=="node.create_configured"
    assert failure["suggestion"]

    unsupported=error_ops.unsupported_capability("unknown.op",["node.create"])
    assert unsupported["code"]=="CAPABILITY_NOT_SUPPORTED"
    assert unsupported["supported_operations"]==["node.create"]


def test_dispatcher_uses_structured_error_helpers():
    text=(ADAPTER_PY/"ai_bridge_houdini"/"dispatcher.py").read_text(encoding="utf-8")
    assert "error_ops.classify_exception" in text
    assert "error_ops.cook_failure" in text
    assert "error_ops.unsupported_capability" in text
