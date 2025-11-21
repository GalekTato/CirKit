from dataclasses import dataclass, field
from typing import Dict, List, Any
from .node import Node
from .components.base import Component

@dataclass
class Netlist:
    nodes: Dict[str, Node] = field(default_factory=dict)
    components: List[Component] = field(default_factory=list)

    def add_node(self, id: str, is_ground: bool = False) -> Node:
        if id not in self.nodes:
            self.nodes[id] = Node(id=id, is_ground=is_ground)
        else:
            # si ya existe, solo actualiza is_ground si es True
            self.nodes[id].is_ground = self.nodes[id].is_ground or is_ground
        return self.nodes[id]

    def add_component(self, c: Component) -> None:
        self.components.append(c)

    def ground_id(self) -> str:
        for k, n in self.nodes.items():
            if n.is_ground:
                return k
        raise ValueError("Falta nodo de tierra (GND).")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte el netlist a un diccionario serializable"""
        elements = []
        for c in self.components:
            elem = {
                "type": c.kind,
                "name": c.id,
                "a": c.n1,
                "b": c.n2,
            }
            if c.kind == "R":
                elem["value"] = c.R
            elif c.kind == "V":
                elem["value"] = c.V
            elif c.kind == "D":
                elem["polarity"] = getattr(c, "polarity", "A_to_K")
            elements.append(elem)
        
        return {
            "nodes": [{"id": n.id, "is_ground": n.is_ground} for n in self.nodes.values()],
            "elements": elements
        }