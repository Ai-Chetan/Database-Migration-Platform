import{r as o}from"./vendor-query-DS6bTBDZ.js";function s(e,t=300){const[r,n]=o.useState(e);return o.useEffect(()=>{const u=setTimeout(()=>n(e),t);return()=>clearTimeout(u)},[e,t]),r}export{s as u};
