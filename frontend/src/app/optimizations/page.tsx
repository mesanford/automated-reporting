"use client"

import { useState, useEffect } from "react"

interface OptimizationPlan {
  id: number
  user_id: str
  connection_id: number
  platform: string
  campaign_name: string
  ad_group_name: string | null
  change_type: string
  original_value: string | null
  proposed_value: string
  status: string
  reasoning: string | null
  is_automated: number
}

export default function OptimizationsPage() {
  const [optimizations, setOptimizations] = useState<OptimizationPlan[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const fetchOptimizations = async () => {
    try {
      const res = await fetch("/api/optimizations?status=pending")
      if (!res.ok) {
        throw new Error("Failed to fetch optimizations")
      }
      const data = await res.json()
      setOptimizations(data)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchOptimizations()
  }, [])

  const handleApproveAndExecute = async (planId: number) => {
    try {
      // 1. Approve
      const approveRes = await fetch(`/api/optimizations/${planId}/approve`, {
        method: "POST",
      })
      if (!approveRes.ok) throw new Error("Approval failed")

      // 2. Execute
      const execRes = await fetch(`/api/optimizations/${planId}/execute`, {
        method: "POST",
      })
      if (!execRes.ok) throw new Error("Execution failed")

      // Refresh the list to remove the executed item
      fetchOptimizations()
    } catch (err: any) {
      alert("Error: " + err.message)
    }
  }

  if (loading) return <div className="p-8 text-center text-gray-500">Loading optimizations...</div>

  return (
    <div className="p-8 max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Pending Optimizations</h1>
        <button 
          onClick={fetchOptimizations}
          className="px-4 py-2 bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
        >
          Refresh
        </button>
      </div>

      {error && <div className="p-4 mb-6 bg-red-50 text-red-700 rounded-md">{error}</div>}

      {optimizations.length === 0 ? (
        <div className="p-12 text-center text-gray-500 border-2 border-dashed border-gray-200 rounded-lg">
          No pending optimizations found.
        </div>
      ) : (
        <div className="grid gap-6 md:grid-cols-2">
          {optimizations.map((opt) => (
            <div key={opt.id} className="bg-white border rounded-lg shadow-sm p-6">
              <div className="flex justify-between items-start mb-4">
                <div>
                  <h3 className="font-semibold text-lg text-gray-900">
                    {opt.change_type.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')}
                  </h3>
                  <p className="text-sm text-gray-500">
                    {opt.platform.charAt(0).toUpperCase() + opt.platform.slice(1)} • {opt.campaign_name}
                    {opt.ad_group_name ? ` • ${opt.ad_group_name}` : ''}
                  </p>
                </div>
                <span className="px-2 py-1 text-xs font-medium rounded-full bg-yellow-100 text-yellow-800">
                  Pending
                </span>
              </div>
              
              <div className="mb-4 bg-gray-50 p-3 rounded text-sm">
                <div className="mb-2">
                  <span className="font-medium text-gray-700">Reasoning: </span>
                  <span className="text-gray-600">{opt.reasoning || "No reasoning provided."}</span>
                </div>
                <div className="flex items-center gap-4 mt-3">
                  <div className="flex-1">
                    <div className="text-xs text-gray-500 mb-1">Current Value</div>
                    <div className="font-mono text-red-600 bg-red-50 px-2 py-1 rounded inline-block">
                      {opt.original_value || "N/A"}
                    </div>
                  </div>
                  <div className="text-gray-400">➔</div>
                  <div className="flex-1">
                    <div className="text-xs text-gray-500 mb-1">Proposed Value</div>
                    <div className="font-mono text-green-600 bg-green-50 px-2 py-1 rounded inline-block">
                      {opt.proposed_value}
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex justify-end pt-2 border-t">
                <button
                  onClick={() => handleApproveAndExecute(opt.id)}
                  className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 font-medium"
                >
                  Approve & Execute
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
