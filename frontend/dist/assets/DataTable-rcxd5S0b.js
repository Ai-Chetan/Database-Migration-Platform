import{a as p,h as g,j as e,i as l,k as h,l as u}from"./vendor-query-DS6bTBDZ.js";import{c as o,p as f,g as c}from"./index-CruFncY9.js";import{a as j}from"./Skeleton-BeaUAURy.js";/**
 * @license lucide-react v0.383.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const y=o("ChevronUp",[["path",{d:"m18 15-6-6-6 6",key:"153udz"}]]);/**
 * @license lucide-react v0.383.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const N=o("ChevronsUpDown",[["path",{d:"m7 15 5 5 5-5",key:"1hf1tw"}],["path",{d:"m7 9 5-5 5 5",key:"sgt6xg"}]]);/**
 * @license lucide-react v0.383.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const v=o("Inbox",[["polyline",{points:"22 12 16 12 14 15 10 15 8 12 2 12",key:"o97t9d"}],["path",{d:"M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z",key:"oot6mr"}]]);function D({columns:d,data:a,isLoading:i,emptyMessage:x="No records found",onRowClick:s}){const[m,b]=p.useState([]),n=g({data:a,columns:d,state:{sorting:m},onSortingChange:b,getCoreRowModel:u(),getSortedRowModel:h()});return i?e.jsx(j,{}):a.length===0?e.jsxs("div",{className:"flex flex-col items-center justify-center rounded border border-border bg-white py-14 text-center",children:[e.jsx(v,{className:"mb-3 h-8 w-8 text-text-tertiary"}),e.jsx("p",{className:"text-body text-text-secondary",children:x})]}):e.jsx("div",{className:"overflow-x-auto rounded border border-border bg-white scrollbar-thin",children:e.jsxs("table",{className:"w-full text-left text-body",children:[e.jsx("thead",{children:n.getHeaderGroups().map(r=>e.jsx("tr",{className:"border-b border-border bg-surface",children:r.headers.map(t=>e.jsx("th",{onClick:t.column.getToggleSortingHandler(),className:c("px-4 py-3 text-tiny font-semibold uppercase tracking-wide text-text-secondary",t.column.getCanSort()&&"cursor-pointer select-none"),children:e.jsxs("div",{className:"flex items-center gap-1",children:[l(t.column.columnDef.header,t.getContext()),t.column.getCanSort()&&({asc:e.jsx(y,{className:"h-3.5 w-3.5"}),desc:e.jsx(f,{className:"h-3.5 w-3.5"})}[t.column.getIsSorted()]??e.jsx(N,{className:"h-3.5 w-3.5 text-text-tertiary"}))]})},t.id))},r.id))}),e.jsx("tbody",{children:n.getRowModel().rows.map(r=>e.jsx("tr",{onClick:()=>s==null?void 0:s(r.original),className:c("border-b border-border last:border-0",s&&"cursor-pointer hover:bg-surface"),children:r.getVisibleCells().map(t=>e.jsx("td",{className:"px-4 py-3 text-text-primary",children:l(t.column.columnDef.cell,t.getContext())},t.id))},r.id))})]})})}export{D};
