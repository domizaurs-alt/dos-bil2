[CmdletBinding()]
param(
    [string]$AcrName = "<acr-name>",
    [string]$Tag = "0.1.0",
    [string]$TestClientImageName = "test-client",
    [string]$ReverseProxyImageName = "reverse-proxy",
    [string]$SubscriptionId = ""
)

$ErrorActionPreference = "Stop"

function Assert-CommandExists {
    param([string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Assert-ValueIsSet {
    param([string]$Name, [string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.StartsWith("<")) {
        throw "Set parameter $Name before running this script."
    }
}

Assert-CommandExists "az"
Assert-CommandExists "docker"
Assert-ValueIsSet "AcrName" $AcrName

if (-not [string]::IsNullOrWhiteSpace($SubscriptionId)) {
    az account set --subscription $SubscriptionId | Out-Null
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TestClientDockerfile = Join-Path $RepoRoot "test-client/app/Dockerfile"
$TestClientContext = Join-Path $RepoRoot "test-client/app"
$ReverseProxyDockerfile = Join-Path $RepoRoot "test-client/reverse-proxy/Dockerfile"
$ReverseProxyContext = Join-Path $RepoRoot "test-client/reverse-proxy"

$AcrServer = az acr show --name $AcrName --query loginServer -o tsv
if ([string]::IsNullOrWhiteSpace($AcrServer)) {
    throw "Could not read ACR login server for $AcrName."
}

az acr login --name $AcrName | Out-Null

$TestClientImage = "${AcrServer}/${TestClientImageName}:${Tag}"
$ReverseProxyImage = "${AcrServer}/${ReverseProxyImageName}:${Tag}"

Write-Host "Building $TestClientImage"
docker build -t $TestClientImage -f $TestClientDockerfile $TestClientContext

Write-Host "Building $ReverseProxyImage"
docker build -t $ReverseProxyImage -f $ReverseProxyDockerfile $ReverseProxyContext

Write-Host "Pushing $TestClientImage"
docker push $TestClientImage

Write-Host "Pushing $ReverseProxyImage"
docker push $ReverseProxyImage

Write-Host ""
Write-Host "Images pushed:"
Write-Host "TEST_CLIENT_IMAGE=$TestClientImage"
Write-Host "REVERSE_PROXY_IMAGE=$ReverseProxyImage"
