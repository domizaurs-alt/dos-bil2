[CmdletBinding()]
param(
    [string]$AcrName = "<acr-name>",
    [string]$ClientResourceGroup = "rg-ddos-bil-client-dev",
    [string]$TestClientContainerApp = "ca-ddos-bil-test-client",
    [string]$ReverseProxyContainerApp = "ca-ddos-bil-reverse-proxy",
    [string]$Tag = "0.1.0",
    [string]$TestClientImageName = "test-client",
    [string]$ReverseProxyImageName = "reverse-proxy",
    [string]$TargetBaseUrl = "",
    [string]$SubscriptionId = "",
    [int]$LongResponseDelayMs = 500,
    [int]$LongResponseMemoryMb = 0,
    [int]$DownloadFileSizeKb = 256,
    [int]$DownloadChunkKb = 64,
    [bool]$TrustSimulatedIp = $true,
    [int]$RateLimitRequests = 20,
    [int]$RateLimitWindowSeconds = 1,
    [int]$BlockDurationSeconds = 30,
    [int]$RequestTimeoutSeconds = 30
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
Assert-ValueIsSet "AcrName" $AcrName
Assert-ValueIsSet "ClientResourceGroup" $ClientResourceGroup
Assert-ValueIsSet "TestClientContainerApp" $TestClientContainerApp
Assert-ValueIsSet "ReverseProxyContainerApp" $ReverseProxyContainerApp

if (-not [string]::IsNullOrWhiteSpace($SubscriptionId)) {
    az account set --subscription $SubscriptionId | Out-Null
}

$AcrServer = az acr show --name $AcrName --query loginServer -o tsv
if ([string]::IsNullOrWhiteSpace($AcrServer)) {
    throw "Could not read ACR login server for $AcrName."
}

$TestClientImage = "${AcrServer}/${TestClientImageName}:${Tag}"
$ReverseProxyImage = "${AcrServer}/${ReverseProxyImageName}:${Tag}"

az acr update --name $AcrName --admin-enabled true | Out-Null
$AcrUser = az acr credential show --name $AcrName --query username -o tsv
$AcrPass = az acr credential show --name $AcrName --query "passwords[0].value" -o tsv

Write-Host "Configuring registry credentials for $TestClientContainerApp"
az containerapp registry set --name $TestClientContainerApp --resource-group $ClientResourceGroup --server $AcrServer --username $AcrUser --password $AcrPass | Out-Null

Write-Host "Configuring registry credentials for $ReverseProxyContainerApp"
az containerapp registry set --name $ReverseProxyContainerApp --resource-group $ClientResourceGroup --server $AcrServer --username $AcrUser --password $AcrPass | Out-Null

$TestClientEnv = @(
    "LONG_RESPONSE_DELAY_MS=$LongResponseDelayMs",
    "LONG_RESPONSE_MEMORY_MB=$LongResponseMemoryMb",
    "DOWNLOAD_FILE_SIZE_KB=$DownloadFileSizeKb",
    "DOWNLOAD_CHUNK_KB=$DownloadChunkKb"
)

Write-Host "Updating $TestClientContainerApp image to $TestClientImage"
az containerapp update --name $TestClientContainerApp --resource-group $ClientResourceGroup --image $TestClientImage --set-env-vars $TestClientEnv | Out-Null

if ([string]::IsNullOrWhiteSpace($TargetBaseUrl)) {
    $TestClientFqdn = az containerapp show --name $TestClientContainerApp --resource-group $ClientResourceGroup --query "properties.configuration.ingress.fqdn" -o tsv
    if ([string]::IsNullOrWhiteSpace($TestClientFqdn)) {
        throw "Could not detect test client FQDN. Re-run with -TargetBaseUrl '<test-client-url>'."
    }
    $TargetBaseUrl = "https://$TestClientFqdn"
}

$ReverseProxyEnv = @(
    "TARGET_BASE_URL=$TargetBaseUrl",
    "TRUST_SIMULATED_IP=$($TrustSimulatedIp.ToString().ToLower())",
    "RATE_LIMIT_REQUESTS=$RateLimitRequests",
    "RATE_LIMIT_WINDOW_SECONDS=$RateLimitWindowSeconds",
    "BLOCK_DURATION_SECONDS=$BlockDurationSeconds",
    "REQUEST_TIMEOUT_SECONDS=$RequestTimeoutSeconds"
)

Write-Host "Updating $ReverseProxyContainerApp image to $ReverseProxyImage"
az containerapp update --name $ReverseProxyContainerApp --resource-group $ClientResourceGroup --image $ReverseProxyImage --set-env-vars $ReverseProxyEnv | Out-Null

$ReverseProxyFqdn = az containerapp show --name $ReverseProxyContainerApp --resource-group $ClientResourceGroup --query "properties.configuration.ingress.fqdn" -o tsv

Write-Host ""
Write-Host "Updated Container Apps:"
Write-Host "TEST_CLIENT_IMAGE=$TestClientImage"
Write-Host "REVERSE_PROXY_IMAGE=$ReverseProxyImage"
Write-Host "TARGET_BASE_URL=$TargetBaseUrl"
Write-Host "REVERSE_PROXY_FQDN=$ReverseProxyFqdn"
