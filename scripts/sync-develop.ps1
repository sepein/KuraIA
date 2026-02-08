git checkout develop
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

git pull
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "develop actualizado."
