// Azure Container Apps deployment for agentic-review-gate.
//
// Demonstrates the pattern: a Container App pulls its image from ACR using a
// user-assigned Managed Identity (no registry passwords), secrets live in Key
// Vault and are surfaced as Key Vault-referenced secrets, and ingress fronts
// the FastAPI app. See infra/README.md -- this IaC has NOT been deployed to a
// live subscription.

@description('Deployment location.')
param location string = resourceGroup().location

@description('Base name used to derive resource names.')
param appName string = 'agentic-review-gate'

@description('Existing Azure Container Registry name (image source).')
param acrName string

@description('Container image, e.g. myacr.azurecr.io/agentic-review-gate:latest')
param containerImage string

@description('Object/principal id of the deploying user, granted Key Vault admin for setup.')
param adminObjectId string

var laName = '${appName}-logs'
var envName = '${appName}-env'
var uamiName = '${appName}-mi'
var kvName = take('${replace(appName, '-', '')}kv${uniqueString(resourceGroup().id)}', 24)

// --- Observability backend for the Container Apps environment ---------------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: laName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// --- User-assigned Managed Identity (ACR pull + Key Vault get/list) ---------
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: uamiName
  location: location
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: acrName
}

// AcrPull role so the MI can pull the image without registry credentials.
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, uami.id, acrPullRoleId)
  scope: acr
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

// --- Key Vault (RBAC mode) for secrets ---------------------------------------
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    publicNetworkAccess: 'Enabled'
  }
}

// MI granted Key Vault Secrets User (get/list on secrets).
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, uami.id, kvSecretsUserRoleId)
  scope: kv
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
  }
}

// Deployer granted Key Vault Secrets Officer so secrets can be seeded.
var kvSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
resource kvAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, adminObjectId, kvSecretsOfficerRoleId)
  scope: kv
  properties: {
    principalId: adminObjectId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsOfficerRoleId)
  }
}

// Placeholder secret; real values are seeded out-of-band (see infra/README.md).
resource webhookSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'github-webhook-secret'
  properties: {
    value: 'replace-me-after-deploy'
  }
}

// --- Container Apps environment ----------------------------------------------
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// --- The Container App -------------------------------------------------------
resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: '${acrName}.azurecr.io'
          identity: uami.id
        }
      ]
      secrets: [
        {
          // Key Vault reference resolved by the MI at runtime.
          name: 'github-webhook-secret'
          keyVaultUrl: webhookSecret.properties.secretUri
          identity: uami.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'AGENT_PROVIDER', value: 'mock' }
            { name: 'GITHUB_WEBHOOK_SECRET', secretRef: 'github-webhook-secret' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
  dependsOn: [
    acrPull
    kvSecretsUser
  ]
}

output appFqdn string = app.properties.configuration.ingress.fqdn
output keyVaultName string = kv.name
output identityClientId string = uami.properties.clientId
