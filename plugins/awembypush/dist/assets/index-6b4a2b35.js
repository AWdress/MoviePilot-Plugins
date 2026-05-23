import { importShared } from './__federation_fn_import-054b33c3.js';
import _sfc_main$1 from './__federation_expose_Config-b2cf0bb4.js';
import _sfc_main$2 from './__federation_expose_Page-57f420bf.js';

true&&(function polyfill() {
    const relList = document.createElement('link').relList;
    if (relList && relList.supports && relList.supports('modulepreload')) {
        return;
    }
    for (const link of document.querySelectorAll('link[rel="modulepreload"]')) {
        processPreload(link);
    }
    new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            if (mutation.type !== 'childList') {
                continue;
            }
            for (const node of mutation.addedNodes) {
                if (node.tagName === 'LINK' && node.rel === 'modulepreload')
                    processPreload(node);
            }
        }
    }).observe(document, { childList: true, subtree: true });
    function getFetchOpts(link) {
        const fetchOpts = {};
        if (link.integrity)
            fetchOpts.integrity = link.integrity;
        if (link.referrerPolicy)
            fetchOpts.referrerPolicy = link.referrerPolicy;
        if (link.crossOrigin === 'use-credentials')
            fetchOpts.credentials = 'include';
        else if (link.crossOrigin === 'anonymous')
            fetchOpts.credentials = 'omit';
        else
            fetchOpts.credentials = 'same-origin';
        return fetchOpts;
    }
    function processPreload(link) {
        if (link.ep)
            // ep marker = processed
            return;
        link.ep = true;
        // prepopulate the load record
        const fetchOpts = getFetchOpts(link);
        fetch(link.href, fetchOpts);
    }
}());

const {resolveDynamicComponent:_resolveDynamicComponent,openBlock:_openBlock,createBlock:_createBlock,resolveComponent:_resolveComponent,withCtx:_withCtx,createVNode:_createVNode} = await importShared('vue');


const {computed,ref} = await importShared('vue');


const _sfc_main = {
  __name: 'App',
  props: {
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
},
  setup(__props) {

const view = ref('config');

const currentView = computed(() => (view.value === 'config' ? _sfc_main$1 : _sfc_main$2));

function toggleView() {
  view.value = view.value === 'config' ? 'page' : 'config';
}

return (_ctx, _cache) => {
  const _component_v_main = _resolveComponent("v-main");
  const _component_v_app = _resolveComponent("v-app");

  return (_openBlock(), _createBlock(_component_v_app, null, {
    default: _withCtx(() => [
      _createVNode(_component_v_main, { class: "bg-grey-lighten-5" }, {
        default: _withCtx(() => [
          (_openBlock(), _createBlock(_resolveDynamicComponent(currentView.value), {
            "initial-config": __props.initialConfig,
            api: __props.api,
            onSwitch: toggleView
          }, null, 40, ["initial-config", "api"]))
        ]),
        _: 1
      })
    ]),
    _: 1
  }))
}
}

};

const {createApp} = await importShared('vue');

const {createVuetify} = await importShared('vuetify');

const vuetify = createVuetify();

createApp(_sfc_main)
  .use(vuetify)
  .mount('#app');
