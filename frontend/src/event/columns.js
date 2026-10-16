// Column definitions for each record type. kind:
//   text | number | choice (fixed options) | list (event list kinds) | secret (Wi-Fi password)
// Older values outside a choice's options (e.g. Band '6 GHz') stay until changed.

export const COLUMNS = {
  wifi: [
    { field: 'ref_id', label: 'ID', kind: 'text', width: 52 },
    { field: 'ssid', label: 'SSID', kind: 'text', width: 170 },
    { field: 'coverage', label: 'Coverage area', kind: 'text', width: 230 },
    { field: 'password_required', label: 'Password?', kind: 'choice', options: ['yes', 'no', 'open network'], width: 110 },
    { field: 'broadcast', label: 'Broadcast?', kind: 'choice', options: ['yes', 'no'], width: 95 },
    { field: 'vlan', label: 'VLAN', kind: 'list', lists: ['vlan'], width: 170 },
    { field: 'band', label: 'Band', kind: 'choice', options: ['5 GHz & 6 GHz', '5 GHz'], width: 120 },
    { field: 'password', label: 'Password value', kind: 'secret', width: 170 },
    { field: 'purpose', label: 'Purpose', kind: 'text', width: 230 },
  ],
  ethernet: [
    { field: 'purpose', label: 'Purpose', kind: 'text', width: 200 },
    { field: 'location', label: 'Location', kind: 'list', lists: ['location', 'building'], width: 190 },
    { field: 'vlan', label: 'VLAN', kind: 'list', lists: ['vlan'], width: 170 },
    { field: 'room', label: 'Area/Room', kind: 'text', width: 90 },
    { field: 'bandwidth_ns', label: 'Bandwidth N–S', kind: 'text', width: 105 },
    { field: 'bandwidth_ew', label: 'Bandwidth E–W', kind: 'text', width: 105 },
    { field: 'capacity', label: 'Capacity', kind: 'text', width: 80 },
    // templateLocked: always 0 / empty in templates (set on the event instead)
    { field: 'ports', label: 'Ethernet points', kind: 'number', width: 90, total: true, templateLocked: true },
    { field: 'notes', label: 'Notes', kind: 'text', width: 300, templateLocked: true },
  ],
  partner: [
    { field: 'partner', label: 'Partner & Exhibitor', kind: 'text', width: 230 },
    { field: 'pavilion', label: 'Pav', kind: 'text', width: 55 },
    { field: 'stand', label: 'Stand', kind: 'text', width: 75 },
    { field: 'solution', label: 'Network solution', kind: 'text', width: 250 },
    { field: 'bandwidth_mbps', label: 'Bandwidth (Mbps)', kind: 'number', width: 100 },
    { field: 'default_cables', label: 'Default cables', kind: 'number', width: 90 },
    { field: 'total_ports', label: 'Total ports', kind: 'number', width: 80 },
    { field: 'wifi', label: 'Wi-Fi', kind: 'number', width: 60 },
    { field: 'vlans', label: 'VLANs', kind: 'number', width: 60 },
    { field: 'notes', label: 'Notes', kind: 'text', width: 220 },
    { field: 'purchased', label: 'Purchased', kind: 'choice', options: ['yes', 'no', 'tbc'], width: 95 },
  ],
}

export const TITLES = {
  wifi: 'Wi-Fi networks',
  ethernet: 'Ethernet requirements',
  partner: 'Partner orders',
  vlan_extension: 'VLAN extensions',
}

export const CHOICE_LABELS = { yes: 'Yes', no: 'No', tbc: 'TBC', 'open network': 'Open network' }

// The name shown for a record in confirmations and messages.
export function recordName(type, data) {
  const key = { wifi: 'ssid', ethernet: 'purpose', partner: 'partner', vlan_extension: 'vlan' }[type]
  return data?.[key] || data?.ref_id || 'this item'
}
