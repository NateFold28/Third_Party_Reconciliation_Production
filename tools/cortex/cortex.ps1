$searchRoots = @(
	(Join-Path $HOME "tools\coco\bin"),
	(Join-Path $HOME "tools\coco")
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.." )).Path

$cortexPath = $null

foreach ($root in $searchRoots) {
	$candidate = Join-Path $root "cortex.exe"
	if (Test-Path $candidate) {
		$cortexPath = $candidate
		break
	}
}

if (-not $cortexPath) {
	$cortexPath = Get-ChildItem (Join-Path $HOME "tools\coco") -Filter "cortex.exe" -File -Recurse -ErrorAction SilentlyContinue |
		Select-Object -ExpandProperty FullName -First 1
}

if (-not $cortexPath) {
	throw "Could not find cortex.exe under $HOME\tools\coco"
}

$hasWorkdirArgument = $args -contains "-w" -or $args -contains "--workdir"

if ($hasWorkdirArgument) {
	& $cortexPath @args
}
else {
	& $cortexPath --workdir $projectRoot @args
}