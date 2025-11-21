import numpy as np

class LinearSolver:
    def solve(self, A, b):
        """
        Resuelve el sistema lineal A·x = b
        
        Intenta múltiples métodos en caso de que uno falle:
        1. numpy.linalg.solve (método directo)
        2. numpy.linalg.lstsq (mínimos cuadrados, más robusto)
        3. SVD para sistemas mal condicionados
        """
        try:
            # Método 1: Resolver directamente
            x = np.linalg.solve(A, b)
            
            # Verificar que la solución sea válida
            residual = np.linalg.norm(A @ x - b)
            if residual > 1e-6:
                print(f"Advertencia: residual alto ({residual}), intentando método alternativo")
                raise np.linalg.LinAlgError("Residual alto")
                
            return x
            
        except np.linalg.LinAlgError:
            # Método 2: Mínimos cuadrados (más robusto)
            try:
                x, residuals, rank, s = np.linalg.lstsq(A, b, rcond=None)
                
                # Verificar el rango de la matriz
                if rank < min(A.shape):
                    print(f"Advertencia: matriz con rango deficiente ({rank}/{min(A.shape)})")
                
                return x
                
            except Exception as e:
                # Método 3: SVD para casos extremos
                try:
                    U, s, Vt = np.linalg.svd(A, full_matrices=False)
                    
                    # Filtrar valores singulares muy pequeños
                    tolerance = 1e-10
                    s_inv = np.array([1/si if si > tolerance else 0 for si in s])
                    
                    # Resolver usando SVD
                    x = Vt.T @ np.diag(s_inv) @ U.T @ b
                    
                    return x
                    
                except Exception as e2:
                    raise ValueError(
                        f"No se pudo resolver el sistema de ecuaciones. "
                        f"El circuito puede tener un error de diseño: {e2}"
                    )