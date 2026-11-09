import {afterEach,expect,it} from "vitest";
import {cleanup,render,screen} from "@testing-library/react";
import {CharacterStack} from "../CharacterStack";
afterEach(cleanup);
it("counts selected people even when their primary photo is shared, without counting extra angles",()=>{
 render(<CharacterStack job={{id:"job",title:"Clip",status:"completed",created_at:1,reference_urls:["https://storage.googleapis.com/front.jpg","https://storage.googleapis.com/side.jpg"],selection_targets:[{id:"a",name:"A",anchor:{x:.2,y:.5,time:0},reference_index:0,reference_indexes:[0,1]},{id:"b",name:"B",anchor:{x:.7,y:.5,time:0},reference_index:0}]}}/>);
 expect(screen.getByRole("group",{name:"2 replacement characters"})).toBeVisible();expect(screen.getAllByRole("img")).toHaveLength(2);expect(screen.queryByRole("group",{name:"3 replacement characters"})).not.toBeInTheDocument();
});
