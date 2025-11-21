from ..analysis.tableau import build_system
from ..analysis.solver import LinearSolver
from ..analysis.checks import run_checks
from ..analysis.results import Solution
from .validation import validate

def simulate(nl) -> Solution:
    """
    Simula el circuito y devuelve la solución con voltajes y corrientes.
    
    Args:
        nl: Netlist del circuito
        
    Returns:
        Solution con voltajes nodales, corrientes y verificaciones
        
    Raises:
        ValidationError: Si el circuito tiene errores de diseño
        ValueError: Si el sistema no se puede resolver
    """
    try:
        # Paso 1: Validar el circuito
        validate(nl)
        
        # Paso 2: Construir el sistema de ecuaciones
        try:
            A, b, meta = build_system(nl)
        except ValueError as e:
            raise ValueError(f"Error al construir el sistema: {e}")
        
        # Paso 3: Resolver el sistema
        try:
            solver = LinearSolver()
            x = solver.solve(A, b)
        except Exception as e:
            raise ValueError(
                f"Error al resolver el sistema de ecuaciones: {e}\n\n"
                "Posibles causas:\n"
                "• Nodos flotantes (sin conexión a GND)\n"
                "• Fuentes de voltaje en cortocircuito\n"
                "• Componentes con valores inválidos\n"
                "• Circuito mal conectado"
            )
        
        # Paso 4: Reconstruir la solución
        sol = meta.reconstruct_solution(x)
        
        # Paso 5: Verificar las leyes de Kirchhoff
        try:
            sol.checks = run_checks(nl, sol)
        except Exception as e:
            # Si falla la verificación, continuar sin checks
            print(f"Advertencia: no se pudieron verificar las leyes: {e}")
            sol.checks = {}
        
        return sol
        
    except Exception as e:
        # Re-lanzar con contexto adicional si es necesario
        if "singular" in str(e).lower():
            raise ValueError(
                "El sistema de ecuaciones es singular. Esto generalmente significa:\n\n"
                "1. Hay nodos flotantes (sin conexión eléctrica a tierra)\n"
                "2. Fuentes de voltaje conectadas directamente (cortocircuito)\n"
                "3. Circuito mal formado o desconectado\n\n"
                "Revisa las conexiones del circuito."
            )
        raise