import ApiKeysCard from './ApiKeysCard'
import GoogleCard from './GoogleCard'
import IntegrationCard from './IntegrationCard'

export default function Dashboard() {
  return (
    <div className="space-y-6">
      <GoogleCard />
      <ApiKeysCard />
      <IntegrationCard />
    </div>
  )
}
