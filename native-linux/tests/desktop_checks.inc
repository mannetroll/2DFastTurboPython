// Included inside Application: actual X11 input drives the production widgets.
// GPU readbacks and the step list exist only under --self-test.
    using CheckStep=std::pair<std::string,std::function<bool(const turbo::ViewState&,const turbo::CanvasInfo&)>>;
    std::vector<CheckStep> desktopChecks;
    Json testLayouts=Json::array();
    uint64_t testGeneration=0,testHash=0;
    float testNumber=0;
    bool reveal(const char* name){
        auto p=controls.at(name);
        if(p.x>=inspectorPos.x&&p.y<inspectorPos.y+20*scale){testScroll=std::max(0.f,inspectorScroll+p.y-inspectorPos.y-50*scale);return false;}
        if(p.x>=inspectorPos.x&&p.y>inspectorPos.y+inspectorSize.y-20*scale){testScroll=std::min(inspectorMaxScroll,inspectorScroll+p.y-inspectorPos.y-inspectorSize.y+50*scale);return false;}
        return true;
    }
    void layoutCheck(const char* name,const turbo::CanvasInfo& geometry){
        check(imageSize.x==imageSize.y,"square field");
        check(std::abs(imagePos.x+imageSize.x/2-viewportPos.x-viewportSize.x/2)<=1,"horizontal centering");
        check(std::abs(imagePos.y+imageSize.y/2-viewportPos.y-viewportSize.y/2)<=1,"vertical centering");
        check(imagePos.y>=viewportPos.y-1&&imagePos.y+imageSize.y<=viewportPos.y+viewportSize.y+1,"full field visible");
        check(focused||std::abs(inspectorSize.x-196*scale)<1,"compact inspector width");
        testLayouts.push_back({{"name",name},{"framebuffer",{geometry.width,geometry.height}},{"scale",scale},
            {"image",{imagePos.x,imagePos.y,imageSize.x,imageSize.y}},{"viewport",{viewportPos.x,viewportPos.y,viewportSize.x,viewportSize.y}},
            {"inspector",{inspectorPos.x,inspectorPos.y,inspectorSize.x,inspectorSize.y}},{"scroll_max",inspectorMaxScroll},{"focused",focused}});
        testCapture=name;
    }
    void initializeDesktopChecks(){
        auto once=[&](std::string name,auto fn){desktopChecks.emplace_back(std::move(name),[fn](const auto& s,const auto& g){fn(s,g);return true;});};
        auto wait=[&](std::string name,auto fn){desktopChecks.emplace_back(std::move(name),fn);};
        auto tap=[&](const char* name,bool inspector=false){desktopChecks.emplace_back(std::string("Click ")+name,[this,name,inspector](const auto&,const auto&){
            if(inspector&&!reveal(name))return false;
            if(std::string(name)=="Undo")testCapture="edit-before-undo";
            click(name);return true;
        });};
        auto key=[&](std::string name,unsigned long symbol,unsigned mods=0){once(std::move(name),[=,this](const auto&,const auto&){platform.key(symbol,mods);});};
        auto menu=[&](const char* name,const char* item){tap(name);tap(item);};
        auto ready=[](const auto& s,const auto&){return s.ready&&s.settled;};
        wait("CUDA/VkFFT initialization and GPU texture",[this](const auto& s,const auto&){return s.ready&&s.settled&&shown&&texture&&fieldUpdates>1;});
        if(args.testExit!="wm"){
            once("Activate lifecycle test window",[this](const auto&,const auto&){platform.show();});
            once("Window activation settles",[](const auto&,const auto&){});
            if(args.testExit=="close-menu"||args.testExit=="quit-menu")tap("File");
            once("Close/quit lifecycle action",[this](const auto&,const auto&){completed=true;
                if(args.testExit=="close-menu")click("Menu Close Window");
                else if(args.testExit=="quit-menu")click("Menu Quit 2DFastTurbo");
                else platform.key(args.testExit=="ctrl-w"?XK_w:XK_q,4);
            });return;
        }
        menu("Window","Menu Minimize");wait("Minimized window",[](const auto&,const auto& g){return !g.visible;});
        once("Restore minimized window",[this](const auto&,const auto&){platform.show();});wait("Window restored",[](const auto&,const auto& g){return g.visible;});
        key("Minimize shortcut",XK_m,4);wait("Ctrl+M minimizes",[](const auto&,const auto& g){return !g.visible;});once("Restore again",[this](const auto&,const auto&){platform.show();});
        once("Initial texture has more than 100 colors",[this](const auto&,const auto&){previousHash=pixels();});
        tap("Run");wait("Run advances",[](const auto& s,const auto&){return s.running&&s.stats.step>=8;});
        tap("Pause");wait("Pause settles",ready);
        once("Remember paused step",[this](const auto& s,const auto&){pausedStep=s.stats.step;});
        once("Pause remains stable",[this](const auto& s,const auto&){check(s.stats.step==pausedStep,"pause stability");});
        tap("Single Step");wait("Single Step exactly once",[this](const auto& s,const auto&){return s.settled&&s.stats.step==pausedStep+1;});
        tap("Reset");wait("Reset reproduces seed and pixels",[this](const auto& s,const auto&){if(!s.settled||s.stats.step||!shown||shown->time)return false;check(pixels()==previousHash,"reset texture");fieldHashes.insert(pixels());return true;});
        for(auto [name,id]:{std::pair{"Speed",1},std::pair{"Enstrophy",2},std::pair{"Vorticity",0}}){
            tap(name,true);wait(std::string("GPU field ")+name,[this,id](const auto&,const auto&){if(display.field!=id||lastDisplay!=display||(id==1&&!shown->hasVelocity))return false;fieldHashes.insert(pixels());return true;});
        }
        once("Three distinct fields",[this](const auto&,const auto&){check(fieldHashes.size()==3,"distinct field textures");});
        for(auto [name,id]:{std::pair{"Inferno",3},std::pair{"Aurora",0},std::pair{"Glacier",2},std::pair{"Ember",1}}){
            once("Remember palette pixels",[this](const auto&,const auto&){testHash=pixels();});tap(name,true);
            wait(std::string("GPU palette ")+name,[this,id](const auto&,const auto&){if(display.palette!=id||lastDisplay!=display)return false;check(pixels()!=testHash,"palette pixels changed");return true;});
        }
        wait("Adjust Sigma",[this](const auto&,const auto&){if(!reveal("Sigma"))return false;auto p=controls.at("Sigma");platform.click(p.x+30*scale,p.y);return true;});wait("Sigma updates paused texture",[this](const auto&,const auto&){return display.sigma!=2.5f&&lastDisplay==display;});
        tap("Color range",true);tap("Option Exposure");
        wait("Exposure mode selected",[this](const auto&,const auto&){return display.colorRange==1;});
        tap("Exposure",true);wait("Exposure updates paused texture",[this](const auto&,const auto&){return display.exposure!=1.2f&&lastDisplay==display;});
        tap("Velocity direction",true);wait("Flow requests a velocity lease",[this](const auto&,const auto&){return display.flow&&shown->hasVelocity&&lastDisplay==display;});
        tap("Velocity direction",true);
        once("Remember generation for live edits",[this](const auto& s,const auto&){testGeneration=s.generation;testNumber=s.config.cfl;});
        tap("CFL",true);wait("CFL edits live without reset",[this](const auto& s,const auto&){check(s.generation==testGeneration,"CFL must not reset");return s.config.cfl!=testNumber;});
        once("Remember Reynolds",[this](const auto& s,const auto&){testNumber=s.config.reynolds;});
        tap("Reynolds",true);wait("Reynolds edits live without reset",[this](const auto& s,const auto&){check(s.generation==testGeneration,"Re must not reset");return std::abs(s.config.reynolds-testNumber)>1;});
        tap("Regulation",true);wait("Regulation enabled",[](const auto& s,const auto&){return s.regulated;});
        tap("Regulation",true);wait("Regulation disabled",[](const auto& s,const auto&){return !s.regulated;});
        tap("Seed",true);key("Select seed",XK_a,4);key("Type seed 3",XK_3);key("Type seed 1",XK_1);key("Type seed 4",XK_4);
        once("Typing does not reset",[this](const auto& s,const auto&){check(s.generation==testGeneration,"typing restarted simulation");check(std::string(seedText)=="314","seed typing");});
        key("Arrow stays in editor",XK_Right);key("F stays in editor",XK_f);key("Remove typed F",XK_BackSpace);key("New seed shortcut stays in editor",XK_r,5);key("R stays in editor",XK_r);key("Space stays in editor",XK_space);
        once("Simulation shortcuts do not consume typing",[this](const auto& s,const auto&){check(s.generation==testGeneration&&!s.running&&s.stats.step==0&&!focused,"editor leaked simulation shortcuts");});
        key("Select edited seed",XK_a,4);key("Replace seed",XK_7);
        menu("Edit","Undo");once("Undo replacement insertion",[this](const auto&,const auto&){check(std::string(seedText).empty(),"menu Undo insertion");});
        menu("Edit","Undo");once("Undo replacement deletion",[this](const auto&,const auto&){check(std::string(seedText)=="314r ","menu Undo deletion");});
        menu("Edit","Redo");menu("Edit","Redo");once("Edit menu Redo",[this](const auto&,const auto&){check(std::string(seedText)=="7","menu Redo");});
        menu("Edit","Select All");menu("Edit","Copy");menu("Edit","Cut");
        once("Edit menu Cut",[this](const auto&,const auto&){check(std::string(seedText).empty(),"menu Cut");});
        menu("Edit","Paste");once("Edit menu Paste",[this](const auto&,const auto&){check(std::string(seedText)=="7","menu Paste");});
        key("Keyboard Select All",XK_a,4);key("Keyboard Copy",XK_c,4);key("Keyboard Cut",XK_x,4);
        once("Keyboard Cut clears selection",[this](const auto&,const auto&){check(std::string(seedText).empty(),"Ctrl+X");});
        key("Keyboard Undo",XK_z,4);once("Keyboard Undo restores cut",[this](const auto&,const auto&){check(std::string(seedText)=="7","Ctrl+Z");});
        key("Keyboard Redo",XK_y,4);once("Keyboard Redo reapplies cut",[this](const auto&,const auto&){check(std::string(seedText).empty(),"Ctrl+Y");});
        key("Keyboard Paste",XK_v,4);once("Keyboard Paste restores text",[this](const auto&,const auto&){check(std::string(seedText)=="7","Ctrl+V");});
        tap("Edit");once("Capture active Edit menu",[this](const auto&,const auto&){testCapture="menu-edit-active";});key("Dismiss Edit menu",XK_Escape);
        key("Commit seed",XK_Return);wait("Seed commits and resets",[this](const auto& s,const auto&){return s.config.seed==7&&s.generation>testGeneration&&s.settled;});
        tap("New seed",true);wait("New seed button",[](const auto& s,const auto&){return s.config.seed==8&&s.settled;});
        key("New seed shortcut",XK_r,5);wait("Ctrl+Shift+R changes seed",[](const auto& s,const auto&){return s.config.seed==9&&s.settled;});
        menu("Simulation","Menu New Initial Condition");wait("New initial condition menu",[](const auto& s,const auto&){return s.config.seed==10&&s.settled;});
        tap("k₀",true);tap("6");wait("K0 resets",[](const auto& s,const auto&){return s.config.k0==6&&s.settled;});
        tap("N",true);tap("256");wait("Resolution resets",[](const auto& s,const auto&){return s.config.n==256&&s.settled;});
        tap("Start spectrum",true);tap("Option KM3 (k⁻³)");wait("KM3 spectrum",[](const auto& s,const auto&){return s.config.startSpectrum==turbo::StartSpectrum::km3&&s.settled;});
        const char* methodOptions[]={"Option IMEX RK3","Option Integrating-factor RK3","Option ETD RK4","Option CNAB2"};
        for(int i=0;i<4;++i){tap("Method",true);tap(methodOptions[i]);
            wait("Integrator selection "+std::to_string(i),[i](const auto& s,const auto&){return int(s.config.integrator)==i&&s.settled;});}
        once("CNAB2 constraints",[this](const auto& s,const auto&){check(s.config.cfl<=.25&&s.config.form==turbo::Form::stress,"CNAB2 constraints");});
        tap("Method",true);tap("Option ETD RK4");wait("Restore ETDRK4",ready);
        tap("Form",true);tap("Option Advective");wait("Form resets",[](const auto& s,const auto&){return s.config.form==turbo::Form::advective&&s.settled;});
        tap("Update interval",true);once("Interval changed",[this](const auto&,const auto&){check(intervalMs!=100,"interval edit");});
        tap("Visualization",true);once("Visualization disabled",[this](const auto&,const auto&){check(!visualize,"visualization checkbox");});
        menu("Simulation","Menu Resume");wait("Solver runs with visualization disabled",[](const auto& s,const auto&){return s.running&&s.stats.step>8;});
        menu("Simulation","Menu Pause");wait("Pause menu",ready);
        menu("View","Menu Show Simulation");wait("Show Simulation restores current paused frame",[this](const auto& s,const auto&){return visualize&&shown&&shown->time==s.stats.time;});
        menu("Simulation","Menu Reset Flow");wait("Reset menu",[](const auto& s,const auto&){return s.settled&&s.stats.step==0;});
        menu("Simulation","Menu Single Step");wait("Step menu",[](const auto& s,const auto&){return s.settled&&s.stats.step==1;});
        // Remove navigation focus with a click on the flow before plain shortcuts.
        once("Focus flow",[this](const auto&,const auto&){platform.click(imagePos.x+imageSize.x/2,imagePos.y+imageSize.y/2);});
        key("Space shortcut",XK_space);wait("Space runs",[](const auto& s,const auto&){return s.running;});
        key("Space pauses",XK_space);wait("Space pause settles",ready);
        once("Remember shortcut step",[this](const auto& s,const auto&){pausedStep=s.stats.step;});key("Right shortcut",XK_Right);
        wait("Right exactly one step",[this](const auto& s,const auto&){return s.settled&&s.stats.step==pausedStep+1;});key("R shortcut",XK_r);wait("R resets",[](const auto& s,const auto&){return s.settled&&s.stats.step==0;});
        once("Remember numeric editor generation",[this](const auto& s,const auto&){testGeneration=s.generation;});
        wait("Ctrl-click numeric CFL editor",[this](const auto&,const auto&){if(!reveal("CFL"))return false;auto p=controls.at("CFL");platform.click(p.x,p.y,true);return true;});
        key("Select numeric CFL",XK_a,4);key("Numeric CFL 1",XK_1);key("Decimal point",XK_period);key("Numeric CFL 2",XK_2);
        menu("Edit","Select All");menu("Edit","Copy");menu("Edit","Cut");menu("Edit","Paste");
        key("Commit numeric CFL",XK_Return);
        once("Numeric Edit menu retains live semantics",[this](const auto& s,const auto&){check(std::abs(s.config.cfl-1.2f)<.001f&&s.generation==testGeneration&&s.stats.step==0,"numeric editor clipboard/live edit");});
        // Invalid seed exercises a real validation error and Dismiss Message.
        tap("Seed",true);key("Select seed for error",XK_a,4);key("Invalid seed",XK_r);key("Commit invalid seed",XK_Return);
        once("Invalid edit is reported",[this](const auto&,const auto&){check(!error.empty(),"invalid edit error");});menu("View","Menu Dismiss Message");
        once("Dismiss Message clears error",[this](const auto&,const auto&){check(error.empty(),"dismiss error");});
        menu("File","Menu About 2DFastTurbo");once("About dialog evidence",[this](const auto&,const auto&){check((ImGui::FindWindowByName("About 2DFastTurbo")&&ImGui::FindWindowByName("About 2DFastTurbo")->Active),"About open");testCapture="about";});tap("About OK");
        once("Set snapshot filename",[this](const auto&,const auto&){auto file=std::filesystem::path(args.output).parent_path()/"snapshot.png";std::snprintf(snapshotPath,sizeof(snapshotPath),"%s",file.c_str());});
        menu("File","Menu Save Snapshot…");tap("Save PNG");
        // Existing evidence is deliberately retained; overwrite is explicitly tested below.
        wait("Snapshot exported",[this](const auto&,const auto&){if((ImGui::FindWindowByName("Replace existing file?")&&ImGui::FindWindowByName("Replace existing file?")->Active)){click("Replace PNG");return false;}return !exportFrame&&std::filesystem::file_size(snapshotPath)>100;});
        once("PNG matches reference export size",[this](const auto&,const auto&){
            png_image png{};png.version=PNG_IMAGE_VERSION;
            check(png_image_begin_read_from_file(&png,snapshotPath)!=0,"read exported PNG");
            bool correct=png.width==1800&&png.height==1800;png_image_free(&png);check(correct,"1800x1800 reference PNG");
        });
        key("Snapshot shortcut",XK_s,4);tap("Save PNG");tap("Replace PNG");wait("Snapshot overwrite confirmed",[this](const auto&,const auto&){return !exportFrame;});
        tap("Snapshot");tap("Cancel PNG");once("Snapshot cancel",[this](const auto&,const auto&){check(!exportFrame,"cancel releases frame lease");});
        for(const char* name:{"File","Edit","Simulation","View","Window"}){
            tap(name);once(std::string("Capture menu ")+name,[this,name](const auto&,const auto&){testCapture=std::string("menu-")+name;});key("Dismiss menu",XK_Escape);
        }
        key("Keyboard menu navigation: Alt",XK_Alt_L);key("Open keyboard menu",XK_Down);
        once("Keyboard navigates menu",[this](const auto&,const auto&){check(!GImGui->OpenPopupStack.empty(),"keyboard menu opened");testCapture="menu-keyboard";});key("Next menu",XK_Right);key("Close keyboard menu",XK_Escape);key("Leave menu bar",XK_Escape);
        // Reset reference visual choices using the same controller/display APIs.
        once("Restore reference visual settings",[this](const auto&,const auto&){edit=controller->view().config;edit.n=2048;edit.k0=10;edit.seed=2;edit.form=turbo::Form::stress;edit.startSpectrum=turbo::StartSpectrum::pao;edit.cfl=3;configure();display=turbo::Display{};controller->velocity(false);intervalMs=100;controller->visualizationInterval(.1);testScroll=0;});wait("Reference field ready",ready);
        tap("Run");wait("Evolve comparable reference field",[](const auto& s,const auto&){return s.stats.time>=.30;});tap("Pause");wait("Pause reference capture",ready);
        once("Resize 1280x720",[this](const auto&,const auto&){platform.resize(1280,720);});wait("1280x720 square and centered",[this](const auto&,const auto& g){if(g.width!=1280||g.height!=720)return false;layoutCheck("1280x720",g);return true;});
        once("Wheel scroll inspector",[this](const auto&,const auto&){for(int i=0;i<12;++i)platform.wheel(inspectorPos.x+50*scale,inspectorPos.y+100*scale,true);});
        once("Inspector scroll reaches numerics and presentation",[this](const auto&,const auto&){check(inspectorScroll>100,"inspector wheel scrolling");testCapture="1280x720-scrolled";});
        tap("Focus");wait("Header focus mode",[this](const auto&,const auto& g){if(!focused)return false;layoutCheck("1280x720-focus",g);return true;});
        tap("File");once("Menus accessible in focus mode",[this](const auto&,const auto&){testCapture="focus-menu";});key("Dismiss focus menu",XK_Escape);
        menu("View","Menu Show Controls");once("Show Controls restores inspector",[this](const auto&,const auto&){check(!focused,"Show Controls");testScroll=0;});
        once("Focus flow for F",[this](const auto&,const auto&){platform.click(imagePos.x+imageSize.x/2,imagePos.y+imageSize.y/2);});key("F focus shortcut",XK_f);
        once("F hides controls",[this](const auto&,const auto&){check(focused,"F focus");});key("F restores controls",XK_f);
        once("Resize 1920x1080",[this](const auto&,const auto&){platform.resize(1920,1080,true);});wait("1920x1080 square and centered",[this](const auto&,const auto& g){if(g.width!=1920||g.height!=1080)return false;layoutCheck("1920x1080",g);return true;});
        once("High DPI 1.5x",[this](const auto&,const auto&){scale=1.5f;presentation::style(scale);});
        once("High DPI square and centered",[this](const auto&,const auto& g){layoutCheck("1920x1080-dpi150",g);});
        menu("View","Menu Focus on Flow");once("High DPI focus",[this](const auto&,const auto& g){check(focused,"high DPI focus");layoutCheck("1920x1080-dpi150-focus",g);});
        menu("View","Menu Show Controls");once("Restore DPI",[this](const auto&,const auto&){scale=1;presentation::style(scale);});
        once("Maximize",[this](const auto&,const auto&){platform.resize(1280,720);platform.maximize(true);});once("Maximized layout",[this](const auto&,const auto& g){layoutCheck("maximized",g);});
        once("Restore size",[this](const auto&,const auto&){platform.maximize(false);});once("Resize reference proportions",[this](const auto&,const auto&){platform.resize(940,900);testScroll=0;});
        wait("Reference proportions",[this](const auto&,const auto& g){if(g.width!=940||g.height!=900)return false;layoutCheck("reference-proportions",g);return true;});
        tap("Run");wait("Close while solver is running",[this](const auto& s,const auto&){if(!s.running||s.stats.step<8)return false;testCapture="reference-running";completed=true;platform.close();return true;});
    }
    void selfTest(const turbo::ViewState& s,const turbo::CanvasInfo& geometry){
        if(desktopChecks.empty())initializeDesktopChecks();
        if(stage>=int(desktopChecks.size())){
            if(completed&&turbo::nowSeconds()-stageTime>3)throw std::runtime_error("Close action did not exit: "+args.testExit);
            return;
        }
        double now=turbo::nowSeconds();auto& step=desktopChecks[stage];
        if(now-stageTime>30)throw std::runtime_error("Self-test timeout: "+step.first);
        if(now-stageTime<.35)return;
        if(step.second(s,geometry))next(step.first.c_str());
    }
