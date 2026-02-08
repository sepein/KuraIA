param(
    [Parameter(Mandatory = $true)]
    [string]$Name
)

$sanitized = $Name.Trim().ToLower() -replace "[^a-z0-9\-_/]+", "-" -replace "-{2,}", "-"
$sanitized = $sanitized.Trim("-")

if ([string]::IsNullOrWhiteSpace($sanitized)) {
    Write-Error "Nombre de feature invalido."
    exit 1
}

git checkout develop
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

git pull
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$branch = "feature/$sanitized"
git checkout -b $branch
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Rama creada: $branch"
