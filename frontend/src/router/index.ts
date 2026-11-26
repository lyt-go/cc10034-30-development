import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 「检索申请」是密文索引工作台内的能力；这些别名路径都直接落到密文索引页，
    // 避免按独立资源路径访问时被 /:moduleKey 当成未知模块。
    { path: '/search', name: 'search-alias', component: WorkbenchView, props: { moduleKey: 'encrypt_index' } },
    { path: '/search_request', name: 'search-request-alias', component: WorkbenchView, props: { moduleKey: 'encrypt_index' } },
    { path: '/search-requests', name: 'search-requests-alias', component: WorkbenchView, props: { moduleKey: 'encrypt_index' } },
    { path: '/index_search', name: 'index-search-alias', component: WorkbenchView, props: { moduleKey: 'encrypt_index' } },
    { path: '/encrypt_index/search', name: 'encrypt-search-alias', component: WorkbenchView, props: { moduleKey: 'encrypt_index' } },
    { path: '/encrypt_index/searches', name: 'encrypt-searches-alias', component: WorkbenchView, props: { moduleKey: 'encrypt_index' } },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
