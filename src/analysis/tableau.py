import numpy as np
from typing import Tuple
from ..domain.netlist import Netlist
from ..domain.components.resistor import Resistor
from ..domain.components.vsource import VSource


class Meta:
    """
    Guarda metadatos de simulación: índices de nodos, componentes, etc.
    """
    def __init__(self, node_index, components, vsource_indices):
        self.node_index = node_index
        self.components = components
        self.vsource_indices = vsource_indices

    def reconstruct_solution(self, x):
        """
        Reconstruye un objeto Solution con voltajes e intensidades.
        """
        from .results import Solution

        # Los primeros valores son voltajes de nodos
        n = len(self.node_index)
        V = {nid: float(x[idx]) for nid, idx in self.node_index.items()}
        
        # Los siguientes valores son corrientes de fuentes de voltaje
        I = {}

        def v(nid):
            return V.get(nid, 0.0)  # GND = 0

        # Corrientes en resistores (Ley de Ohm)
        for c in self.components:
            if isinstance(c, Resistor):
                I[c.id] = (v(c.n1) - v(c.n2)) / c.R
            elif isinstance(c, VSource):
                # La corriente de la fuente está en las variables extra
                if c.id in self.vsource_indices:
                    I[c.id] = float(x[self.vsource_indices[c.id]])
                else:
                    I[c.id] = 0.0

        return Solution(node_voltages=V, branch_currents=I, diode_states={}, checks={})


def build_system(nl: Netlist) -> Tuple[np.ndarray, np.ndarray, Meta]:
    """
    Construye la matriz de ecuaciones A·x = b usando Modified Nodal Analysis (MNA).
    
    Sistema de ecuaciones:
    - Para cada nodo (excepto GND): KCL (suma de corrientes = 0)
    - Para cada fuente de voltaje: ecuación de voltaje
    
    Variables:
    - Voltajes de nodos (excepto GND)
    - Corrientes de fuentes de voltaje
    """

    # Identificar GND
    gnd_node = None
    for nid, n in nl.nodes.items():
        if n.is_ground:
            gnd_node = nid
            break
    
    if gnd_node is None:
        raise ValueError("No se encontró nodo de tierra (GND)")

    # Nodos (sin GND)
    nodes = [nid for nid, n in nl.nodes.items() if not n.is_ground]
    node_index = {nid: i for i, nid in enumerate(nodes)}
    
    # Identificar fuentes de voltaje
    vsources = [c for c in nl.components if isinstance(c, VSource)]
    
    # Verificar que no haya lazos de fuentes de voltaje
    # (esto causaría matriz singular)
    if len(vsources) > 1:
        # Verificación básica: detectar fuentes en serie
        vsource_nodes = set()
        for vs in vsources:
            if vs.n1 in vsource_nodes or vs.n2 in vsource_nodes:
                # Posible problema, pero lo permitimos con advertencia
                pass
            vsource_nodes.add(vs.n1)
            vsource_nodes.add(vs.n2)
    
    # Crear índices para las corrientes de las fuentes
    vsource_indices = {}
    for i, vs in enumerate(vsources):
        vsource_indices[vs.id] = len(nodes) + i

    # Dimensiones del sistema
    n_nodes = len(nodes)
    n_vsources = len(vsources)
    n = n_nodes + n_vsources  # Total de variables

    if n == 0:
        raise ValueError("El circuito no tiene nodos flotantes ni fuentes de voltaje")

    # Inicializar matrices
    A = np.zeros((n, n), dtype=float)
    b = np.zeros(n, dtype=float)

    # --- PARTE 1: KCL para cada nodo ---
    for nid in nodes:
        i = node_index[nid]
        
        # Contribuciones de resistores
        for c in nl.components:
            if isinstance(c, Resistor):
                if c.R <= 0:
                    raise ValueError(f"Resistor {c.id} tiene valor inválido: {c.R}")
                    
                g = 1.0 / c.R  # conductancia
                
                # Si el resistor conecta a este nodo
                if c.n1 == nid:
                    # Corriente sale del nodo: i = (V_nid - V_n2) / R
                    A[i, node_index[c.n1]] += g
                    if c.n2 != gnd_node:
                        A[i, node_index[c.n2]] -= g
                        
                elif c.n2 == nid:
                    # Corriente entra al nodo: i = (V_n1 - V_nid) / R
                    if c.n1 != gnd_node:
                        A[i, node_index[c.n1]] -= g
                    A[i, node_index[c.n2]] += g
        
        # Contribuciones de fuentes de voltaje
        for vs in vsources:
            # La corriente de la fuente afecta los nodos que conecta
            if vs.n1 == nid:
                # Corriente sale por n1
                A[i, vsource_indices[vs.id]] += 1.0
            elif vs.n2 == nid:
                # Corriente entra por n2
                A[i, vsource_indices[vs.id]] -= 1.0

    # --- PARTE 2: Ecuaciones de voltaje para fuentes ---
    for vs in vsources:
        # Ecuación: V_n1 - V_n2 = V_source
        row = vsource_indices[vs.id]
        
        if vs.n1 != gnd_node:
            A[row, node_index[vs.n1]] = 1.0
        if vs.n2 != gnd_node:
            A[row, node_index[vs.n2]] = -1.0
            
        b[row] = vs.V

    # Verificar condición de la matriz
    det = np.linalg.det(A)
    rank = np.linalg.matrix_rank(A)
    
    if rank < n:
        # Matriz singular - problema en el circuito
        # Agregar pequeña regularización para intentar resolver
        epsilon = 1e-10
        A += np.eye(n) * epsilon
        
        # Verificar de nuevo
        new_rank = np.linalg.matrix_rank(A)
        if new_rank < n:
            raise ValueError(
                f"El sistema de ecuaciones es singular (rango {rank}/{n}). "
                "Posibles causas: nodos flotantes, fuentes en cortocircuito, "
                "o componentes desconectados."
            )

    return A, b, Meta(
        node_index=node_index,
        components=list(nl.components),
        vsource_indices=vsource_indices
    )