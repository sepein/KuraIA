# Flujo Git local recomendado

Este flujo te permite trabajar como si `master` estuviera protegido, aunque la proteccion en GitHub no este activa.

## 1) Configuracion inicial (una vez)

Desde la raiz del repo:

```powershell
git config core.hooksPath .githooks
```

Con esto, antes de cada `git push` se ejecuta:
- compilacion Python (`py_compile`)
- tests unitarios (`unittest`)

Si falla algo, el push se bloquea.

## 2) Flujo diario

1. Sincroniza base:

```powershell
.\scripts\sync-develop.ps1
```

2. Crea rama de trabajo:

```powershell
.\scripts\new-feature.ps1 "mi-cambio"
```

3. Implementa cambios y haz commits pequenos:

```powershell
git add -A
git commit -m "feat: descripcion corta"
```

4. Sube rama:

```powershell
git push -u origin feature/mi-cambio
```

5. Abre PR de `feature/*` hacia `develop` (cuando tengas habilitado GitHub con PR flow).

6. Cuando `develop` este validado, merge a `master`.

## 3) Regla operativa

- No hacer commits directos en `master` salvo emergencias.
- Trabajar siempre en `feature/*`.
- Mantener CI en verde antes de merge.
